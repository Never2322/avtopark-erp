// «Внести часы»: механик ставит сотруднику часы работы или ремонта. Ставок и сумм здесь нет: деньги видит только офис.
// Сумма считается сама по тарифу из карточки сотрудника; убрать свою запись можно в первые двое суток.
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Hammer, Trash2, Truck } from "lucide-react";
import { ApiError, get, post } from "../api";
import { fmtDate, fmtNum, plural } from "../format";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, PageHeader, QueryState, useForm, useToast } from "../ui";

interface Person { id: number; name: string; position: string }
interface Recent { id: number; work_date: string; employee: string; kind: "work" | "repair"; kind_label: string; hours: number; vehicle: string; description: string; can_delete: boolean }
interface EnterData {
  today: string; min_date: string; undo_hours: number; people: Person[]; vehicles: { id: number; label: string }[]; recent: Recent[];
}

const newId = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`);

function EnterForm({ data }: { data: EnterData }) {
  const qc = useQueryClient();
  const toast = useToast();
  const sendId = useRef(newId());
  const f = useForm({ employee_id: "", work_date: data.today, kind: "work", hours: "", vehicle_id: "", description: "" });
  const repair = f.values.kind === "repair";
  const m = useMutation({
    mutationFn: () => post<{ item: Recent; warnings: string[]; duplicate: boolean }>("/payroll/enter", {
      employee_id: Number(f.values.employee_id), work_date: f.values.work_date, kind: f.values.kind, hours: f.values.hours.trim(),
      vehicle_id: f.values.vehicle_id ? Number(f.values.vehicle_id) : null, description: f.values.description, client_id: sendId.current,
    }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["hours-entry"] });
      for (const k of ["my-time", "driver-home", "worklog", "timesheet"]) qc.invalidateQueries({ queryKey: [k] });
      toast.success(`Записано: ${r.item.employee}, ${fmtNum(r.item.hours)} ч`);
      for (const w of r.warnings) toast.error(w);
      sendId.current = newId();                                  // следующая запись — новая отправка
      f.setValues((v) => ({ ...v, employee_id: "", hours: "", vehicle_id: "", description: "" }));
    },
    onError: f.fail,
  });
  const ready = !!f.values.employee_id && !!f.values.hours.trim();
  const submit = () => { if (ready) { f.clear(); m.mutate(); } };
  return (
    <Card title="Новая запись">
      <form className="stack" noValidate onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <Field label="Сотрудник" error={f.errors.employee_id}>
          <select className="input" value={f.values.employee_id} onChange={(e) => f.set("employee_id", e.target.value)}>
            <option value="">Выберите сотрудника</option>
            {data.people.map((p) => <option key={p.id} value={p.id}>{p.name}{p.position ? `, ${p.position}` : ""}</option>)}
          </select>
        </Field>
        <div className="seg" role="group" aria-label="Что делал">
          {(["work", "repair"] as const).map((k) => (
            <button key={k} type="button" className={`seg-btn ${f.values.kind === k ? "on" : ""}`} aria-pressed={f.values.kind === k} onClick={() => f.set("kind", k)}>{k === "work" ? "Работа" : "Ремонт"}</button>
          ))}
        </div>
        <div className="form-grid">
          <Field label="Дата" error={f.errors.work_date}>
            <input className="input" type="date" value={f.values.work_date} min={data.min_date} max={data.today} onChange={(e) => f.set("work_date", e.target.value)} />
          </Field>
          <Field label={repair ? "Сколько часов ремонтировал" : "Сколько часов работал"} error={f.errors.hours} hint="Например 8 или 2,5">
            <input className="input" inputMode="decimal" value={f.values.hours} maxLength={5} onChange={(e) => f.set("hours", e.target.value)} />
          </Field>
        </div>
        <Field label="Техника (по желанию)" error={f.errors.vehicle_id}>
          <select className="input" value={f.values.vehicle_id} onChange={(e) => f.set("vehicle_id", e.target.value)}>
            <option value="">Не указывать</option>
            {data.vehicles.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
          </select>
        </Field>
        <Field label={repair ? <>Что делали на ремонте <span style={{ color: "var(--error)" }} aria-hidden>*</span></> : "Комментарий (по желанию)"} error={f.errors.description}>
          <textarea className="input" rows={3} maxLength={500} value={f.values.description} onChange={(e) => f.set("description", e.target.value)}
            placeholder={repair ? "Например: заменил шланг гидравлики, масло и фильтры" : "Например: вывоз снега, два объекта"} />
        </Field>
        <div className="row">
          <Button variant="primary" loading={m.isPending} disabled={!ready} onClick={submit}>Записать</Button>
        </div>
      </form>
    </Card>
  );
}

export function HoursEntry() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["hours-entry"], queryFn: () => get<EnterData>("/payroll/enter") });
  const [del, setDel] = useState<Recent | null>(null);
  const d = q.data;
  const remove = async () => {
    if (!del) return;
    try {
      await post(`/payroll/enter/${del.id}/delete`);
      for (const k of ["hours-entry", "my-time", "driver-home", "worklog", "timesheet"]) qc.invalidateQueries({ queryKey: [k] });
      toast.success("Запись убрана");
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Не удалось убрать запись"); }
  };
  return (
    <>
      <PageHeader title="Внести часы" sub="Часы работы и ремонта сотрудников. Сумма считается сама по тарифу сотрудника, ставки и деньги здесь не показываются" />
      <QueryState q={q}>
        {d && (
          <div className="stack">
            {d.people.length === 0 && <Alert tone="warning">Нет сотрудников, которым можно внести часы. Сотрудник должен числиться в вашем юрлице (карточка сотрудника, «Где работает»).</Alert>}
            <EnterForm data={d} />
            <Card flush title={`Внесено вами за последние 7 дней`}>
              {d.recent.length === 0 ? <Empty icon={Hammer} title="Пока ничего не внесено">Здесь будут записи, которые вы внесли за последнюю неделю.</Empty> : (
                <ul className="time-list">
                  {d.recent.map((it) => (
                    <li key={it.id} className="time-row">
                      <div className="time-main">
                        <strong>{fmtDate(it.work_date)}</strong>
                        <span>{it.employee}</span>
                        <Badge tone={it.kind === "repair" ? "warning" : "info"}>{it.kind_label}</Badge>
                        <span>{fmtNum(it.hours)} {plural(Math.round(it.hours), "час", "часа", "часов")}</span>
                        {it.vehicle && <span className="muted small"><Truck size={12} aria-hidden /> {it.vehicle}</span>}
                      </div>
                      {it.description && <div className="muted small">{it.description}</div>}
                      {it.can_delete && (
                        <div className="time-pay">
                          <Button variant="ghost" size="sm" icon={Trash2} aria-label="Убрать запись" title={`Убрать запись (можно в первые ${d.undo_hours} ч)`} onClick={() => setDel(it)}>Убрать</Button>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        )}
      </QueryState>
      {del && <ConfirmModal title="Убрать запись" danger confirmLabel="Убрать" reasonRequired={false}
        text={`${fmtDate(del.work_date)}, ${del.employee}, ${del.kind_label.toLowerCase()}, ${fmtNum(del.hours)} ч. Из заработка сотрудника эта запись уйдёт.`} onConfirm={async () => { await remove(); }} onClose={() => setDel(null)} />}
    </>
  );
}
