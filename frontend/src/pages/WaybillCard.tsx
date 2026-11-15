import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Ban, CheckCircle2, Pencil, Play, Wrench } from "lucide-react";
import { get, patch, post } from "../api";
import { useAuth } from "../auth";
import { fmtDate, fmtDateTime, fmtNum } from "../format";
import { useDriverOptions, VehicleSelect } from "../components/pickers";
import { Alert, Badge, Button, Card, ConfirmModal, Field, Modal, numOrNull, PageHeader, NotFound, QueryState, Spinner, useForm, useToast, WaybillStatus } from "../ui";
import { runText, SHIFTS } from "./Waybills";

interface Detail {
  id: number; number_label: string; legal_entity_id: number; status: string; status_label: string; work_date: string; shift: string; shift_label: string;
  vehicle: { id: number; plate: string; name: string; status: string }; driver: { id: number; name: string }; route: string; work_site: string;
  start_km: number | null; end_km: number | null; run_km: number | null; start_hours: number | null; end_hours: number | null; run_hours: number | null;
  opened_at: string | null; closed_at: string | null; fuel_start_l: number | null; fuel_added_l: number | null; fuel_end_l: number | null; note: string; cancel_reason: string;
  closed_by: string | null; vehicle_current: { km: number | null; hours: number | null };
  actions: { edit: boolean; open: boolean; close: boolean; cancel: boolean; correct: boolean };
  corrections: { id: number; reason: string; before: Record<string, string | null>; after: Record<string, string | null>; created_at: string; author: string | null }[];
}

const FIELD_LABEL: Record<string, string> = {
  end_km: "Конечный пробег, км", end_hours: "Моточасы на конец, м·ч", fuel_start_l: "Топливо на начало, л", fuel_added_l: "Заправлено, л", fuel_end_l: "Топливо на конец, л",
  route: "Маршрут", work_site: "Объект работ", note: "Заметка",
};
const s = (v: number | null) => (v === null ? "" : String(v));
const shownValue = (v: string | null | undefined) => (v === null || v === undefined ? "пусто" : /^-?\d+(\.\d+)?$/.test(v) ? fmtNum(Number(v)) : v);
const litres = (v: number | null) => (v === null ? "—" : `${fmtNum(v)} л`);

function OpenModal({ w, onClose }: { w: Detail; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ start_km: s(w.vehicle_current.km), start_hours: s(w.vehicle_current.hours), fuel_start_l: s(w.fuel_start_l) });
  const m = useMutation({
    mutationFn: () => post(`/waybills/${w.id}/open`, { start_km: numOrNull(f.values.start_km), start_hours: numOrNull(f.values.start_hours), fuel_start_l: numOrNull(f.values.fuel_start_l) }),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Лист открыт"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Открыть путевой лист" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" icon={Play} loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Открыть</Button></>}>
      <div className="stack">
        {f.formError && !f.errors.start_km && !f.errors.start_hours && !f.errors.fuel_start_l && <Alert tone="error">{f.formError}</Alert>}
        <div className="muted">Впишите показания с приборов на начало смены. Если они больше последних в системе, значения запишутся в журнал машины.</div>
        <div className="form-grid">
          <Field label="Пробег на начало, км" error={f.errors.start_km} hint={w.vehicle_current.km !== null ? `В системе: ${fmtNum(w.vehicle_current.km)} км` : "Если пробег не ведётся, оставьте пустым"}>
            <input className="input" inputMode="decimal" value={f.values.start_km} onChange={(e) => f.set("start_km", e.target.value)} />
          </Field>
          <Field label="Моточасы на начало, м·ч" error={f.errors.start_hours} hint={w.vehicle_current.hours !== null ? `В системе: ${fmtNum(w.vehicle_current.hours)} м·ч` : "Если моточасы не ведутся, оставьте пустым"}>
            <input className="input" inputMode="decimal" value={f.values.start_hours} onChange={(e) => f.set("start_hours", e.target.value)} />
          </Field>
          <Field label="Топливо в баке, л" error={f.errors.fuel_start_l}><input className="input" inputMode="decimal" value={f.values.fuel_start_l} onChange={(e) => f.set("fuel_start_l", e.target.value)} /></Field>
        </div>
      </div>
    </Modal>
  );
}

function CloseModal({ w, onClose }: { w: Detail; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ end_km: "", end_hours: "", fuel_added_l: s(w.fuel_added_l), fuel_end_l: "", note: w.note });
  const m = useMutation({
    mutationFn: () => post<{ readings_need_review: string[] }>(`/waybills/${w.id}/close`, {
      end_km: numOrNull(f.values.end_km), end_hours: numOrNull(f.values.end_hours), fuel_added_l: numOrNull(f.values.fuel_added_l), fuel_end_l: numOrNull(f.values.fuel_end_l), note: f.values.note,
    }),
    onSuccess: (r) => {
      qc.invalidateQueries();
      toast.success(r.readings_need_review?.length ? "Лист закрыт. Большой скачок показаний ушёл на проверку" : "Лист закрыт");
      onClose();
    },
    onError: f.fail,
  });
  return (
    <Modal title="Закрыть путевой лист" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" icon={CheckCircle2} loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Закрыть лист</Button></>}>
      <div className="stack">
        {f.formError && !["end_km", "end_hours", "fuel_added_l", "fuel_end_l"].some((k) => f.errors[k]) && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          {w.start_km !== null && <Field label="Пробег на конец, км" error={f.errors.end_km} hint={`Начало смены: ${fmtNum(w.start_km)} км`}><input className="input" inputMode="decimal" value={f.values.end_km} onChange={(e) => f.set("end_km", e.target.value)} /></Field>}
          {w.start_hours !== null && <Field label="Моточасы на конец, м·ч" error={f.errors.end_hours} hint={`Начало смены: ${fmtNum(w.start_hours)} м·ч`}><input className="input" inputMode="decimal" value={f.values.end_hours} onChange={(e) => f.set("end_hours", e.target.value)} /></Field>}
          <Field label="Заправлено за смену, л" error={f.errors.fuel_added_l}><input className="input" inputMode="decimal" value={f.values.fuel_added_l} onChange={(e) => f.set("fuel_added_l", e.target.value)} /></Field>
          <Field label="Осталось в баке, л" error={f.errors.fuel_end_l} hint={w.fuel_start_l !== null ? `В начале смены: ${fmtNum(w.fuel_start_l)} л` : undefined}><input className="input" inputMode="decimal" value={f.values.fuel_end_l} onChange={(e) => f.set("fuel_end_l", e.target.value)} /></Field>
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} maxLength={2000} onChange={(e) => f.set("note", e.target.value)} /></Field>
        </div>
        <div className="muted small">После закрытия лист меняется только через «Исправить» с причиной, а прежние значения остаются в истории.</div>
      </div>
    </Modal>
  );
}

function EditModal({ w, onClose }: { w: Detail; onClose: () => void }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const draft = w.status === "draft";
  const chooseDriver = draft && can("waybills.create", w.legal_entity_id);
  const drivers = useDriverOptions(w.legal_entity_id, chooseDriver);
  const f = useForm({ vehicle_id: String(w.vehicle.id), driver_id: String(w.driver.id), work_date: w.work_date, shift: w.shift, route: w.route, work_site: w.work_site, note: w.note, fuel_start_l: s(w.fuel_start_l), fuel_added_l: s(w.fuel_added_l) });
  const m = useMutation({
    mutationFn: () => {
      const v = f.values;
      const body: Record<string, unknown> = { route: v.route, work_site: v.work_site, note: v.note };
      if (draft) Object.assign(body, { vehicle_id: Number(v.vehicle_id), work_date: v.work_date, shift: v.shift, fuel_start_l: numOrNull(v.fuel_start_l), ...(chooseDriver ? { driver_id: Number(v.driver_id) } : {}) });
      else body.fuel_added_l = numOrNull(v.fuel_added_l);
      return patch(`/waybills/${w.id}`, body);
    },
    onSuccess: () => { qc.invalidateQueries(); toast.success("Сохранено"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Изменить путевой лист" wide onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Сохранить</Button></>}>
      <div className="stack">
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        {!draft && <div className="muted small">Лист уже открыт: машину, водителя и дату менять нельзя. Можно поправить маршрут, объект, заметку и заправку.</div>}
        <div className="form-grid">
          {draft && <VehicleSelect value={f.values.vehicle_id} entityId={w.legal_entity_id} error={f.errors.vehicle_id} onChange={(v) => f.set("vehicle_id", v)} />}
          {chooseDriver && (
            <Field label="Водитель" error={f.errors.driver_id}>
              <select className="input" value={f.values.driver_id} onChange={(e) => f.set("driver_id", e.target.value)}>
                {drivers.data?.items.map((d) => <option key={d.id} value={d.id}>{d.full_name}</option>)}
              </select>
            </Field>
          )}
          {draft && <Field label="Дата работы" error={f.errors.work_date}><input className="input" type="date" value={f.values.work_date} onChange={(e) => f.set("work_date", e.target.value)} /></Field>}
          {draft && <Field label="Смена" error={f.errors.shift}><select className="input" value={f.values.shift} onChange={(e) => f.set("shift", e.target.value)}>{SHIFTS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}</select></Field>}
          <Field label="Маршрут" error={f.errors.route}><input className="input" value={f.values.route} maxLength={300} onChange={(e) => f.set("route", e.target.value)} /></Field>
          <Field label="Объект работ" error={f.errors.work_site}><input className="input" value={f.values.work_site} maxLength={200} onChange={(e) => f.set("work_site", e.target.value)} /></Field>
          {draft ? <Field label="Топливо в баке, л" error={f.errors.fuel_start_l}><input className="input" inputMode="decimal" value={f.values.fuel_start_l} onChange={(e) => f.set("fuel_start_l", e.target.value)} /></Field>
            : <Field label="Заправлено, л" error={f.errors.fuel_added_l}><input className="input" inputMode="decimal" value={f.values.fuel_added_l} onChange={(e) => f.set("fuel_added_l", e.target.value)} /></Field>}
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} maxLength={2000} onChange={(e) => f.set("note", e.target.value)} /></Field>
        </div>
      </div>
    </Modal>
  );
}

function CorrectModal({ w, onClose }: { w: Detail; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const initial = { end_km: s(w.end_km), end_hours: s(w.end_hours), fuel_start_l: s(w.fuel_start_l), fuel_added_l: s(w.fuel_added_l), fuel_end_l: s(w.fuel_end_l), route: w.route, work_site: w.work_site, note: w.note, reason: "" };
  const f = useForm(initial);
  const m = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { reason: f.values.reason };
      for (const k of ["end_km", "end_hours", "fuel_start_l", "fuel_added_l", "fuel_end_l"] as const) if (f.values[k] !== initial[k] && f.values[k].trim() !== "") body[k] = numOrNull(f.values[k]);
      for (const k of ["route", "work_site", "note"] as const) if (f.values[k] !== initial[k]) body[k] = f.values[k];
      return post(`/waybills/${w.id}/correct`, body);
    },
    onSuccess: () => { qc.invalidateQueries(); toast.success("Исправление записано, прежние значения сохранены в истории"); onClose(); },
    onError: f.fail,
  });
  const changed = (Object.keys(initial) as (keyof typeof initial)[]).some((k) => k !== "reason" && f.values[k] !== initial[k]);
  return (
    <Modal title="Исправить закрытый лист" wide onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={!changed || f.values.reason.trim().length < 3} onClick={() => { f.clear(); m.mutate(); }}>Записать исправление</Button></>}>
      <div className="stack">
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <Alert tone="warning">Исправление остаётся в истории навсегда: что было, что стало, кто и почему. Показания машины исправляются отдельной записью.</Alert>
        <div className="form-grid">
          {w.start_km !== null && <Field label="Пробег на конец, км" error={f.errors.end_km}><input className="input" inputMode="decimal" value={f.values.end_km} onChange={(e) => f.set("end_km", e.target.value)} /></Field>}
          {w.start_hours !== null && <Field label="Моточасы на конец, м·ч" error={f.errors.end_hours}><input className="input" inputMode="decimal" value={f.values.end_hours} onChange={(e) => f.set("end_hours", e.target.value)} /></Field>}
          <Field label="Топливо на начало, л" error={f.errors.fuel_start_l}><input className="input" inputMode="decimal" value={f.values.fuel_start_l} onChange={(e) => f.set("fuel_start_l", e.target.value)} /></Field>
          <Field label="Заправлено, л" error={f.errors.fuel_added_l}><input className="input" inputMode="decimal" value={f.values.fuel_added_l} onChange={(e) => f.set("fuel_added_l", e.target.value)} /></Field>
          <Field label="Топливо на конец, л" error={f.errors.fuel_end_l}><input className="input" inputMode="decimal" value={f.values.fuel_end_l} onChange={(e) => f.set("fuel_end_l", e.target.value)} /></Field>
          <Field label="Маршрут" error={f.errors.route}><input className="input" value={f.values.route} onChange={(e) => f.set("route", e.target.value)} /></Field>
          <Field label="Объект работ" error={f.errors.work_site}><input className="input" value={f.values.work_site} onChange={(e) => f.set("work_site", e.target.value)} /></Field>
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} onChange={(e) => f.set("note", e.target.value)} /></Field>
          <Field label="Причина исправления (обязательно)" full error={f.errors.reason}><textarea className="input" value={f.values.reason} maxLength={300} onChange={(e) => f.set("reason", e.target.value)} /></Field>
        </div>
      </div>
    </Modal>
  );
}

export function WaybillCard() {
  const { id } = useParams();
  const wid = Number(id);
  const qc = useQueryClient();
  const nav = useNavigate();
  const [modal, setModal] = useState<"open" | "close" | "edit" | "cancel" | "correct" | null>(null);
  const q = useQuery({ queryKey: ["waybill", wid], enabled: Number.isInteger(wid), queryFn: () => get<Detail>(`/waybills/${wid}`) });
  const cancel = useMutation({ mutationFn: (reason: string) => post(`/waybills/${wid}/cancel`, { reason }), onSuccess: () => qc.invalidateQueries() });
  const w = q.data;
  if (q.isLoading) return <Spinner />;
  if (!Number.isInteger(wid)) return <NotFound what="Путевой лист" />;
  if (q.error || !w) return <QueryState q={q} what="Путевой лист"><></></QueryState>;

  return (
    <>
      <PageHeader crumb={<Link to="/waybills"><ArrowLeft size={13} aria-hidden /> Путевые листы</Link>}
        title={<span className="row wrap" style={{ gap: 12 }}>Путевой лист №{w.number_label}<WaybillStatus code={w.status} label={w.status_label} /></span>}
        sub={`${w.vehicle.plate} · ${w.driver.name} · ${fmtDate(w.work_date)}, ${w.shift_label.toLowerCase()} смена`}
        actions={<>
          {w.actions.open && <Button variant="primary" icon={Play} onClick={() => setModal("open")}>Открыть лист</Button>}
          {w.actions.close && <Button variant="primary" icon={CheckCircle2} onClick={() => setModal("close")}>Закрыть лист</Button>}
          {w.actions.edit && <Button icon={Pencil} onClick={() => setModal("edit")}>Изменить</Button>}
          {w.actions.correct && <Button icon={Wrench} onClick={() => setModal("correct")}>Исправить</Button>}
          {w.actions.cancel && <Button variant="danger" icon={Ban} onClick={() => setModal("cancel")}>Аннулировать</Button>}
        </>} />
      {w.status === "cancelled" && <Alert tone="warning">Лист аннулирован. Причина: {w.cancel_reason || "не указана"}</Alert>}
      <div className="grid cols-2" style={{ marginTop: 16, alignItems: "start" }}>
        <Card title="Смена">
          <dl className="kv">
            <dt>Машина</dt><dd><Link to={`/vehicles/${w.vehicle.id}`}><strong>{w.vehicle.plate}</strong></Link> <span className="muted">{w.vehicle.name}</span></dd>
            <dt>Водитель</dt><dd>{w.driver.name}</dd>
            <dt>Дата, смена</dt><dd>{fmtDate(w.work_date)}, {w.shift_label.toLowerCase()}</dd>
            <dt>Маршрут</dt><dd>{w.route || <span className="muted">не указан</span>}</dd>
            <dt>Объект работ</dt><dd>{w.work_site || <span className="muted">не указан</span>}</dd>
            <dt>Открыт</dt><dd>{fmtDateTime(w.opened_at)}</dd>
            <dt>Закрыт</dt><dd>{w.closed_at ? <>{fmtDateTime(w.closed_at)}{w.closed_by && <span className="muted"> · {w.closed_by}</span>}</> : "—"}</dd>
            <dt>Заметка</dt><dd style={{ whiteSpace: "pre-wrap" }}>{w.note || <span className="muted">нет</span>}</dd>
          </dl>
        </Card>
        <Card title="Показания и топливо">
          <dl className="kv">
            <dt>Пробег</dt><dd>{w.start_km !== null ? <>{fmtNum(w.start_km)} → {w.end_km !== null ? fmtNum(w.end_km) : "…"} км</> : <span className="muted">не ведётся</span>}</dd>
            <dt>Моточасы</dt><dd>{w.start_hours !== null ? <>{fmtNum(w.start_hours)} → {w.end_hours !== null ? fmtNum(w.end_hours) : "…"} м·ч</> : <span className="muted">не ведутся</span>}</dd>
            <dt>За смену</dt><dd><strong>{runText(w)}</strong></dd>
            <dt>Топливо в начале</dt><dd>{litres(w.fuel_start_l)}</dd>
            <dt>Заправлено</dt><dd>{litres(w.fuel_added_l)}</dd>
            <dt>Топливо в конце</dt><dd>{litres(w.fuel_end_l)}</dd>
          </dl>
        </Card>
      </div>
      {w.corrections.length > 0 && (
        <Card title={<>Исправления <Badge tone="warning">{w.corrections.length}</Badge></>}>
          <ul className="timeline">
            {[...w.corrections].reverse().map((c) => (
              <li key={c.id}>
                <span className="dot mint"><Pencil size={12} aria-hidden /></span>
                <div>
                  <div><strong>Причина:</strong> {c.reason}</div>
                  {Object.keys(c.after).map((k) => <div key={k} className="small">{FIELD_LABEL[k] ?? k}: <span className="muted">{shownValue(c.before[k])}</span> → <strong>{shownValue(c.after[k])}</strong></div>)}
                  <div className="muted small">{fmtDateTime(c.created_at)} · {c.author ?? "система"}</div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {modal === "open" && <OpenModal w={w} onClose={() => setModal(null)} />}
      {modal === "close" && <CloseModal w={w} onClose={() => setModal(null)} />}
      {modal === "edit" && <EditModal w={w} onClose={() => setModal(null)} />}
      {modal === "correct" && <CorrectModal w={w} onClose={() => setModal(null)} />}
      {modal === "cancel" && <ConfirmModal title="Аннулировать путевой лист?" danger confirmLabel="Аннулировать" reasonLabel="Причина (обязательно)"
        text="Аннулированный лист остаётся в списке с причиной, но вернуть его в работу нельзя." onConfirm={(r) => cancel.mutateAsync(r).then(() => nav(`/waybills/${wid}`))} onClose={() => setModal(null)} />}
    </>
  );
}
