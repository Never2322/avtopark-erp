// «Выход на работу»: водитель отвечает, выйдет ли он в этот день; директор и диспетчер видят ответы всех на выбранный день.
// У офиса есть право отметить выход за человека (позвонил, написал): такая отметка помечается «Отметили в офисе».
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarCheck, Check, ChevronLeft, ChevronRight, UserCheck, X } from "lucide-react";
import { ApiError, errorText, get, post, put } from "../api";
import { useAuth } from "../auth";
import { fmtDate, fmtDateTime, todayIso } from "../format";
import { useEntityParam, useUrlState } from "../hooks";
import { Alert, Badge, Button, Card, Empty, Field, FilterChips, Modal, PageHeader, QueryState, Table, useForm, useToast, type Tone } from "../ui";

type Status = "yes" | "no" | "none";
interface Plan { status: Status; status_label: string; reason: string; source: string; source_label: string; answered_at: string | null }
interface MyDay extends Plan { date: string; weekday: string; off: boolean; is_today: boolean }
interface MyData { today: string; max_date: string; name: string; days: MyDay[] }
interface Row extends Plan { employee_id: number; name: string; position: string; vehicles: string[] }
interface DayList {
  date: string; weekday: string; today: string; items: Row[]; counts: { yes: number; no: number; none: number; total: number };
  entities: { id: number; name: string }[]; can_edit: boolean; min_date: string; max_date: string;
}

const TONE: Record<Status, Tone> = { yes: "success", no: "error", none: "warning" };
const shiftDay = (iso: string, n: number) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const dayWord = (iso: string, today: string) => (iso === today ? "сегодня" : iso === shiftDay(today, 1) ? "завтра" : "");
const refresh = (qc: ReturnType<typeof useQueryClient>) => qc.invalidateQueries({ queryKey: ["attendance"] });

/** Окно «Не выйду»: причина обязательна, чтобы директор знал, чего ждать. Для офиса так же, плюс выбор «выйдет / не выйдет». */
function AnswerModal({ title, date, initial, choose, onSave, onClose }: {
  title: string; date: string; initial: Plan | null; choose?: boolean;
  onSave: (v: { will_come: boolean; reason: string }) => Promise<unknown>; onClose: () => void;
}) {
  const f = useForm({ will_come: choose ? initial?.status !== "no" : false, reason: initial?.status === "no" ? initial.reason : "" });
  const [busy, setBusy] = useState(false);
  const go = async () => {
    f.clear(); setBusy(true);
    try { await onSave({ will_come: f.values.will_come, reason: f.values.reason }); onClose(); } catch (e) { f.fail(e); } finally { setBusy(false); }
  };
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" loading={busy} onClick={go}>Сохранить</Button>
    </>}>
      <form className="stack" noValidate onSubmit={(e) => { e.preventDefault(); void go(); }}>
        <p className="muted" style={{ margin: 0 }}>{fmtDate(date)}</p>
        {f.formError && !f.errors.reason && <Alert tone="error">{f.formError}</Alert>}
        {choose && (
          <div className="seg" role="group" aria-label="Ответ">
            <button type="button" className={`seg-btn ${f.values.will_come ? "on" : ""}`} aria-pressed={f.values.will_come} onClick={() => f.set("will_come", true)}>Выйдет</button>
            <button type="button" className={`seg-btn ${!f.values.will_come ? "on" : ""}`} aria-pressed={!f.values.will_come} onClick={() => f.set("will_come", false)}>Не выйдет</button>
          </div>
        )}
        {!f.values.will_come && (
          <Field label="Причина" error={f.errors.reason} hint="Например: болею, отпросился, нет машины">
            <input className="input" value={f.values.reason} maxLength={300} autoFocus onChange={(e) => f.set("reason", e.target.value)} />
          </Field>
        )}
      </form>
    </Modal>
  );
}

function useMineAnswer() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (v: { date: string; will_come: boolean; reason: string }) => post("/attendance/mine", v),
    onSuccess: (_r, v) => { refresh(qc); toast.success(v.will_come ? "Записано: вы выйдете" : "Записано: вы не выйдете"); },
    onError: (e) => toast.error(errorText(e)),
  });
}

/** Карточка на главной водителя: ответ на завтра в одно касание. */
export function AttendanceCard() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["attendance", "mine"], queryFn: () => get<MyData>("/attendance/mine"), enabled: can("attendance.mark_own") });
  const answer = useMineAnswer();
  const [ask, setAsk] = useState<MyDay | null>(null);
  if (!can("attendance.mark_own")) return null;
  const d = q.data?.days[1];
  if (!d) return null;
  return (
    <Card className="att-card">
      <div className="row wrap" style={{ gap: 10, justifyContent: "space-between" }}>
        <div>
          <strong>Выйдете завтра на работу?</strong>
          <div className="muted small">{d.weekday}, {fmtDate(d.date)}</div>
        </div>
        {d.status !== "none" && <Badge tone={TONE[d.status]}>{d.status === "yes" ? "Выйду" : "Не выйду"}</Badge>}
      </div>
      <div className="att-actions">
        <Button variant={d.status === "yes" ? "primary" : "default"} size="lg" icon={Check} loading={answer.isPending && answer.variables?.will_come === true}
          onClick={() => answer.mutate({ date: d.date, will_come: true, reason: "" })}>Выйду</Button>
        <Button variant={d.status === "no" ? "danger" : "default"} size="lg" icon={X} onClick={() => setAsk(d)}>Не выйду</Button>
      </div>
      {d.status === "no" && d.reason && <div className="muted small">Причина: {d.reason}</div>}
      <Link className="small" to="/attendance">Другие дни</Link>
      {ask && <AnswerModal title="Не выйду на работу" date={ask.date} initial={ask} onClose={() => setAsk(null)}
        onSave={(v) => answer.mutateAsync({ date: ask.date, ...v })} />}
    </Card>
  );
}

function MyAttendance() {
  const q = useQuery({ queryKey: ["attendance", "mine"], queryFn: () => get<MyData>("/attendance/mine") });
  const answer = useMineAnswer();
  const [ask, setAsk] = useState<MyDay | null>(null);
  const days = q.data?.days ?? [];
  return (
    <>
      <PageHeader title="Выход на работу" sub="Отметьте, выйдете ли вы на работу в эти дни. Директор и диспетчер увидят ответ. Пока день не закончился, ответ можно поменять." />
      <QueryState q={q}>
        <div className="att-list">
          {days.map((d) => (
            <Card key={d.date} className={`att-day ${d.off ? "off" : ""}`}>
              <div className="att-head">
                <div>
                  <strong>{d.weekday}, {fmtDate(d.date)}</strong>
                  {dayWord(d.date, q.data!.today) && <span className="muted small"> · {dayWord(d.date, q.data!.today)}</span>}
                </div>
                <Badge tone={TONE[d.status]}>{d.status === "none" ? "Не отмечено" : d.status === "yes" ? "Выйду" : "Не выйду"}</Badge>
              </div>
              {d.status === "no" && d.reason && <div className="muted small">Причина: {d.reason}</div>}
              {d.source === "staff" && <div className="muted small">{d.source_label}</div>}
              <div className="att-actions">
                <Button variant={d.status === "yes" ? "primary" : "default"} icon={Check} loading={answer.isPending && answer.variables?.date === d.date && answer.variables.will_come}
                  onClick={() => answer.mutate({ date: d.date, will_come: true, reason: "" })}>Выйду</Button>
                <Button variant={d.status === "no" ? "danger" : "default"} icon={X} onClick={() => setAsk(d)}>Не выйду</Button>
              </div>
            </Card>
          ))}
        </div>
      </QueryState>
      {ask && <AnswerModal title="Не выйду на работу" date={ask.date} initial={ask} onClose={() => setAsk(null)}
        onSave={(v) => answer.mutateAsync({ date: ask.date, ...v })} />}
    </>
  );
}

function OfficeAttendance() {
  const qc = useQueryClient();
  const toast = useToast();
  const entity = useEntityParam();
  const [s, setS] = useUrlState({ date: "", show: "all" });
  const date = s.date || todayIso();
  const q = useQuery({ queryKey: ["attendance", "day", date, entity], queryFn: () => get<DayList>("/attendance", { date, legal_entity_id: entity }), placeholderData: (p) => p });
  const [edit, setEdit] = useState<Row | null>(null);
  const d = q.data;
  const rows = d ? d.items.filter((r) => s.show === "all" || r.status === s.show) : [];
  const setFor = useMutation({
    mutationFn: (v: { id: number; will_come: boolean; reason: string }) => put(`/attendance/${v.id}`, { date, will_come: v.will_come, reason: v.reason }),
    onSuccess: () => { refresh(qc); toast.success("Отмечено"); },
  });
  const go = (iso: string) => setS({ date: iso === todayIso() ? "" : iso });
  const today = d?.today ?? todayIso();
  return (
    <>
      <PageHeader title="Выход на работу" sub="Кто выйдет в выбранный день. Водители отвечают сами; кто не ответил, виден отдельно." />
      <div className="toolbar">
        <div className="row" style={{ gap: 6 }}>
          <Button icon={ChevronLeft} aria-label="Предыдущий день" disabled={!!d && date <= d.min_date} onClick={() => go(shiftDay(date, -1))} />
          <input className="input compact" type="date" aria-label="День" value={date} min={d?.min_date} max={d?.max_date} onChange={(e) => e.target.value && go(e.target.value)} />
          <Button icon={ChevronRight} aria-label="Следующий день" disabled={!!d && date >= d.max_date} onClick={() => go(shiftDay(date, 1))} />
          <Button variant={date === today ? "primary" : "default"} onClick={() => go(today)}>Сегодня</Button>
          <Button variant={date === shiftDay(today, 1) ? "primary" : "default"} onClick={() => go(shiftDay(today, 1))}>Завтра</Button>
        </div>
      </div>
      <QueryState q={q}>
        {d && (
          <div className="stack">
            <div className="grid cols-3 pay-kpis">
              <Card><span className="muted small">Выйдут</span><span className="pay-big">{d.counts.yes}</span><span className="muted small">из {d.counts.total}</span></Card>
              <Card><span className="muted small">Не выйдут</span><span className="pay-big">{d.counts.no}</span><span className="muted small">{d.weekday}, {fmtDate(d.date)}</span></Card>
              <Card><span className="muted small">Не ответили</span><span className="pay-big">{d.counts.none}</span><span className="muted small">{d.counts.none ? "позвоните или отметьте за них" : "все ответили"}</span></Card>
            </div>
            <Card flush>
              <div style={{ padding: "12px 14px" }}>
                <FilterChips value={s.show} onChange={(v) => setS({ show: v })}
                  options={[{ id: "all", label: "Все", count: d.counts.total }, { id: "yes", label: "Выйдут", count: d.counts.yes },
                    { id: "no", label: "Не выйдут", count: d.counts.no }, { id: "none", label: "Не ответили", count: d.counts.none }]} />
              </div>
              {rows.length === 0 ? (
                <Empty icon={UserCheck} title={d.counts.total === 0 ? "В этот день некого ждать" : "Никого в этом отборе"}>
                  {d.counts.total === 0 ? "Здесь появятся водители юрлица, которые работают в этот день." : <Button onClick={() => setS({ show: "all" })}>Показать всех</Button>}
                </Empty>
              ) : (
                <Table rows={rows} rowKey={(r) => r.employee_id}
                  columns={[
                    { key: "n", header: "Сотрудник", render: (r) => <><strong>{r.name}</strong>{r.position && <div className="muted small">{r.position}</div>}</> },
                    { key: "v", header: "Техника", hideSm: true, render: (r) => (r.vehicles.length ? r.vehicles.join(", ") : <span className="muted">—</span>) },
                    { key: "s", header: "Ответ", render: (r) => <Badge tone={TONE[r.status]}>{r.status_label}</Badge> },
                    { key: "r", header: "Причина", hideSm: true, render: (r) => r.reason || <span className="muted">—</span> },
                    { key: "w", header: "Кто и когда", hideSm: true, render: (r) => (r.answered_at ? <span className="muted small">{r.source_label}, {fmtDateTime(r.answered_at)}</span> : <span className="muted">—</span>) },
                    { key: "x", header: "", render: (r) => d.can_edit ? <Button size="sm" icon={CalendarCheck} onClick={() => setEdit(r)}>{r.status === "none" ? "Отметить" : "Изменить"}</Button> : null },
                  ]} />
              )}
            </Card>
            <p className="muted small">В списке водители, которые работают в юрлице в этот день, и все, кто уже ответил.</p>
          </div>
        )}
      </QueryState>
      {edit && <AnswerModal choose title={`Выход: ${edit.name}`} date={date} initial={edit} onClose={() => setEdit(null)}
        onSave={(v) => setFor.mutateAsync({ id: edit.employee_id, ...v }).catch((e) => { throw e instanceof ApiError ? e : new ApiError(0, "error", errorText(e)); })} />}
    </>
  );
}

export function Attendance() {
  const { can } = useAuth();
  return can("attendance.view") ? <OfficeAttendance /> : <MyAttendance />;
}
