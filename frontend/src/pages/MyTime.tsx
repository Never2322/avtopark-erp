// «Мои часы» водителя и форма «Ремонтные работы»: водитель сам отмечает, сколько часов работал и сколько ремонтировал.
// Без подтверждения: сумма считается сразу по тарифу; править и отменять запись может диспетчер (в первые двое суток — и сам водитель).
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Hammer, Plus, Trash2, Truck } from "lucide-react";
import { ApiError, get, post } from "../api";
import { fmtDate, fmtMoney, fmtNum, plural, todayIso } from "../format";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, Modal, PageHeader, QueryState, useForm, useToast } from "../ui";
import { useAuth } from "../auth";
import { useUrlState } from "../hooks";

export interface TimeItem {
  id: number; work_date: string; kind: "work" | "repair"; kind_label: string; hours: number; vehicle: string; description: string; source: string; office_changed?: boolean;
  rate_text: string; amount: number; no_rate: boolean; can_delete: boolean;
}
export interface TimeTariff { id: number; name: string; unit: "hour" | "day"; rate: number }
export interface TimeData {
  month: string; title: string; today: string; min_date: string; delete_hours: number; items: TimeItem[];
  totals: { work_h: number; repair_h: number; work: number; repair: number; total: number };
  tariffs: { work: TimeTariff | null; repair: TimeTariff | null }; vehicles: { id: number; label: string }[]; can_create: boolean;
}

const newId = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`);
const shiftMonth = (m: string, delta: number) => { const [y, mo] = m.split("-").map(Number); const n = y * 12 + mo - 1 + delta; return `${Math.floor(n / 12)}-${String((n % 12) + 1).padStart(2, "0")}`; };
export const tariffText = (t: TimeTariff | null) => (t ? `${t.name}: ${fmtMoney(t.rate)} ₽ ${t.unit === "hour" ? "в час" : "в сутки"}` : "не назначен");
const refresh = (qc: ReturnType<typeof useQueryClient>) => { for (const k of ["my-time", "driver-home", "worklog", "timesheet"]) qc.invalidateQueries({ queryKey: [k] }); };

export function TimeEntryModal({ kind, data, onClose }: { kind: "work" | "repair"; data: TimeData; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const id = useRef(newId());
  const f = useForm({ work_date: data.today, kind: kind as string, hours: "", vehicle_id: data.vehicles.length === 1 ? String(data.vehicles[0].id) : "", description: "" });
  const repair = f.values.kind === "repair";
  const tariff = repair ? data.tariffs.repair : data.tariffs.work;
  const m = useMutation({
    mutationFn: () => post<{ item: TimeItem; warnings: string[] }>("/payroll/mine", {
      work_date: f.values.work_date, kind: f.values.kind, hours: f.values.hours.trim(), vehicle_id: f.values.vehicle_id ? Number(f.values.vehicle_id) : null,
      description: f.values.description, client_id: id.current,
    }),
    onSuccess: (r) => {
      refresh(qc);
      toast.success(`Записано: ${fmtNum(r.item.hours)} ч${r.item.amount ? `, ${fmtMoney(r.item.amount)} ₽` : ""}`);
      for (const w of r.warnings) toast.error(w);
      onClose();
    },
    onError: f.fail,
  });
  const hoursNum = Number(f.values.hours.replace(",", "."));
  const preview = tariff && hoursNum > 0 ? (tariff.unit === "hour" ? hoursNum * tariff.rate : tariff.rate) : null;
  return (
    <Modal title={repair ? "Ремонтные работы" : "Часы работы"} onClose={onClose} footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" loading={m.isPending} disabled={!f.values.hours.trim()} onClick={() => { f.clear(); m.mutate(); }}>Записать</Button>
    </>}>
      <form className="stack" noValidate onSubmit={(e) => { e.preventDefault(); if (f.values.hours.trim()) { f.clear(); m.mutate(); } }}>
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <div className="seg" role="group" aria-label="Что делали">
          {(["work", "repair"] as const).map((k) => (
            <button key={k} type="button" className={`seg-btn ${f.values.kind === k ? "on" : ""}`} aria-pressed={f.values.kind === k} onClick={() => f.set("kind", k)}>{k === "work" ? "Работа" : "Ремонт"}</button>
          ))}
        </div>
        <div className="form-grid">
          <Field label="Дата" error={f.errors.work_date}><input className="input" type="date" value={f.values.work_date} min={data.min_date} max={data.today} onChange={(e) => f.set("work_date", e.target.value)} /></Field>
          <Field label={repair ? "Сколько часов ремонтировали" : "Сколько часов работали"} error={f.errors.hours} hint="Например 8 или 2,5">
            <input className="input" inputMode="decimal" value={f.values.hours} maxLength={5} autoFocus onChange={(e) => f.set("hours", e.target.value)} />
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
        {tariff ? (
          <p className="muted small">{tariffText(tariff)}.{preview !== null && <> К начислению: <strong>{fmtMoney(preview)} ₽</strong></>}</p>
        ) : (
          <Alert tone="warning">Тариф для «{repair ? "ремонт" : "работа"}» вам пока не назначен. Часы запишутся, а сумма появится, когда диспетчер назначит тариф.</Alert>
        )}
      </form>
    </Modal>
  );
}

/** Кнопка «Ремонтные работы» (и «Отметить часы»): сама загружает нужное и открывает форму. */
export function TimeButtons({ kinds = ["repair"], size }: { kinds?: ("work" | "repair")[]; size?: "lg" }) {
  const { can } = useAuth();
  const [kind, setKind] = useState<"work" | "repair" | null>(null);
  const q = useQuery({ queryKey: ["my-time", ""], queryFn: () => get<TimeData>("/payroll/mine"), enabled: can("worklog.create_own") });
  if (!can("worklog.create_own")) return null;
  return (
    <>
      {kinds.map((k) => (
        <Button key={k} size={size} icon={k === "repair" ? Hammer : Plus} disabled={!q.data} onClick={() => setKind(k)}>{k === "repair" ? "Ремонтные работы" : "Часы работы"}</Button>
      ))}
      {q.data && kind && <TimeEntryModal kind={kind} data={q.data} onClose={() => setKind(null)} />}
    </>
  );
}

export function MyTime() {
  const qc = useQueryClient();
  const toast = useToast();
  const [s, setS] = useUrlState({ month: "" });
  const month = s.month || todayIso().slice(0, 7);
  const q = useQuery({ queryKey: ["my-time", month], queryFn: () => get<TimeData>("/payroll/mine", { month }), placeholderData: (p) => p });
  const [add, setAdd] = useState<"work" | "repair" | null>(null);
  const [del, setDel] = useState<TimeItem | null>(null);
  const d = q.data;
  const remove = async () => {
    if (!del) return;
    try { await post(`/payroll/mine/${del.id}/delete`); refresh(qc); toast.success("Запись убрана"); } catch (e) { toast.error(e instanceof ApiError ? e.message : "Не удалось убрать запись"); }
  };
  return (
    <>
      <PageHeader title="Мои часы" sub="Сколько часов вы работали и ремонтировали. Сумма считается сразу по вашему тарифу, а в конце месяца идёт в зарплату"
        actions={d?.can_create && (
          <>
            <Button variant="primary" icon={Plus} onClick={() => setAdd("work")}>Часы работы</Button>
            <Button icon={Hammer} onClick={() => setAdd("repair")}>Ремонтные работы</Button>
          </>
        )} />
      <div className="toolbar">
        <div className="row" style={{ gap: 6 }}>
          <Button icon={ChevronLeft} aria-label="Прошлый месяц" onClick={() => setS({ month: shiftMonth(month, -1) })} />
          <input className="input compact" type="month" aria-label="Месяц" value={month} onChange={(e) => e.target.value && setS({ month: e.target.value })} />
          <Button icon={ChevronRight} aria-label="Следующий месяц" onClick={() => setS({ month: shiftMonth(month, 1) })} />
        </div>
      </div>
      <QueryState q={q}>
        {d && (
          <div className="stack">
            <div className="grid cols-3 pay-kpis">
              <Card><span className="muted small">Работа</span><strong className="pay-big">{fmtNum(d.totals.work_h)} ч</strong><span>{fmtMoney(d.totals.work)} ₽</span></Card>
              <Card><span className="muted small">Ремонт</span><strong className="pay-big">{fmtNum(d.totals.repair_h)} ч</strong><span>{fmtMoney(d.totals.repair)} ₽</span></Card>
              <Card><span className="muted small">Итого за {d.title}</span><strong className="pay-big">{fmtMoney(d.totals.total)} ₽</strong><span className="muted small">до вычета налогов</span></Card>
            </div>
            <p className="muted small">Ваши тарифы. Работа: {tariffText(d.tariffs.work)}. Ремонт: {tariffText(d.tariffs.repair)}.</p>
            {(!d.tariffs.work || !d.tariffs.repair) && <Alert tone="warning">Не назначен тариф: {[!d.tariffs.work && "за работу", !d.tariffs.repair && "за ремонт"].filter(Boolean).join(" и ")}. Пока его нет, часы записываются, а сумма равна нулю. Скажите диспетчеру.</Alert>}
            <Card flush title={`Записи за ${d.title}`}>
              {d.items.length === 0 ? <Empty icon={Hammer} title="Записей за этот месяц нет">Отметьте часы работы или ремонта кнопками сверху.</Empty> : (
                <ul className="time-list">
                  {d.items.map((it) => (
                    <li key={it.id} className="time-row">
                      <div className="time-main">
                        <strong>{fmtDate(it.work_date)}</strong>
                        <Badge tone={it.kind === "repair" ? "warning" : "info"}>{it.kind_label}</Badge>
                        {it.office_changed && <Badge>тип изменён в офисе</Badge>}
                        <span>{fmtNum(it.hours)} {plural(Math.round(it.hours), "час", "часа", "часов")}</span>
                        {it.vehicle && <span className="muted small"><Truck size={12} aria-hidden /> {it.vehicle}</span>}
                      </div>
                      {it.description && <div className="muted small">{it.description}</div>}
                      <div className="time-pay">
                        <strong>{it.no_rate ? <Badge tone="error">нет тарифа</Badge> : `${fmtMoney(it.amount)} ₽`}</strong>
                        <span className="muted small">{it.rate_text}{it.source === "staff" ? " · внесли в офисе" : ""}</span>
                        {it.can_delete && <Button variant="ghost" size="sm" icon={Trash2} aria-label="Убрать запись" title={`Убрать запись (можно в первые ${d.delete_hours} ч)`} onClick={() => setDel(it)} />}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        )}
      </QueryState>
      {d && add && <TimeEntryModal kind={add} data={d} onClose={() => setAdd(null)} />}
      {del && <ConfirmModal title="Убрать запись" danger confirmLabel="Убрать" reasonRequired={false}
        text={`${fmtDate(del.work_date)}, ${del.kind_label.toLowerCase()}, ${fmtNum(del.hours)} ч. Сумма из заработка уйдёт.`} onConfirm={async () => { await remove(); }} onClose={() => setDel(null)} />}
    </>
  );
}
