import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArrowLeft, CheckCircle2, Gauge, History, Pencil, Plus, RotateCcw, UserPlus, Wrench, XCircle } from "lucide-react";
import { get, post, patch } from "../api";
import { fmtDate, fmtDateTime, fmtNum, readingUnit, todayIso } from "../format";
import { Page, useVehicleTypes } from "../hooks";
import {
  Alert, Badge, Button, Card, ConfirmModal, Empty, Field, Modal, numOrNull, PageHeader, NotFound, QueryState, Spinner, Table, Tabs, useForm, useToast, VehicleStatus,
} from "../ui";
import { reading, VehicleRow } from "./Vehicles";
import { RequestsTable } from "./Requests";
import { WaybillsTable } from "./Waybills";

interface Detail extends VehicleRow {
  note: string; department_id: number | null; status_changed_at: string | null;
  actions: { edit: boolean; archive: boolean; change_status: boolean; assign: boolean; add_reading: boolean; correct_reading: boolean; review_reading: boolean; create_request: boolean };
}
interface Reading {
  id: number; kind: string; kind_label: string; value: number; recorded_at: string; source_label: string; author: string | null; note: string;
  is_anomaly: boolean; review_state: string; review_comment: string; corrects_id: number | null; superseded_by: number | null; effective: boolean; waybill_id: number | null;
}
interface Hist { id: number; status: string; status_label: string; from_label: string | null; reason: string; changed_at: string; by: string | null; request_number: number | null; request_id: number | null }
interface Assign { id: number; employee_id: number; employee: string; role: string; started_on: string; ended_on: string | null; active: boolean }

type TabId = "overview" | "readings" | "history" | "drivers" | "requests" | "waybills";

function StatusModal({ v, onClose }: { v: Detail; onClose: () => void }) {
  const types = useVehicleTypes();
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ status: v.status, reason: "" });
  const m = useMutation({
    mutationFn: () => post(`/vehicles/${v.id}/status`, f.values),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Статус изменён"); onClose(); },
    onError: f.fail,
  });
  const needs = ["in_repair", "awaiting_repair", "written_off"].includes(f.values.status);
  return (
    <Modal title="Сменить статус" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" loading={m.isPending} disabled={f.values.status === v.status} onClick={() => { f.clear(); m.mutate(); }}>Сохранить</Button></>}>
      <div className="stack">
        {f.formError && !f.errors.reason && <Alert tone="error">{f.formError}</Alert>}
        <Field label="Новый статус">
          <select className="input" value={f.values.status} onChange={(e) => f.set("status", e.target.value)}>
            {types.data?.statuses.map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}
          </select>
        </Field>
        <Field label={needs ? "Причина (обязательно)" : "Причина (по желанию)"} error={f.errors.reason}>
          <textarea className="input" value={f.values.reason} onChange={(e) => f.set("reason", e.target.value)} maxLength={300} />
        </Field>
        {f.values.status === "written_off" && <Alert tone="warning">Списанную машину нельзя вернуть в работу. Если ошиблись, обратитесь к администратору.</Alert>}
      </div>
    </Modal>
  );
}

function EditModal({ v, onClose }: { v: Detail; onClose: () => void }) {
  const types = useVehicleTypes();
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ type_id: String(v.type.id), plate: v.plate, internal_no: v.internal_no, make: v.make, model: v.model, vin: v.vin ?? "", year: v.year ? String(v.year) : "", note: v.note });
  const m = useMutation({
    mutationFn: () => patch(`/vehicles/${v.id}`, { ...f.values, type_id: Number(f.values.type_id), year: numOrNull(f.values.year), department_id: v.department_id }),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Сохранено"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Изменить данные техники" wide onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Сохранить</Button></>}>
      <div className="stack">
        {f.formError && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          <Field label="Тип техники" error={f.errors.type_id}>
            <select className="input" value={f.values.type_id} onChange={(e) => f.set("type_id", e.target.value)}>{types.data?.items.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
          </Field>
          <Field label="Госномер" error={f.errors.plate}><input className="input" value={f.values.plate} onChange={(e) => f.set("plate", e.target.value)} /></Field>
          <Field label="Марка" error={f.errors.make}><input className="input" value={f.values.make} onChange={(e) => f.set("make", e.target.value)} /></Field>
          <Field label="Модель" error={f.errors.model}><input className="input" value={f.values.model} onChange={(e) => f.set("model", e.target.value)} /></Field>
          <Field label="Внутренний номер" error={f.errors.internal_no}><input className="input" value={f.values.internal_no} onChange={(e) => f.set("internal_no", e.target.value)} /></Field>
          <Field label="VIN" error={f.errors.vin}><input className="input" value={f.values.vin} onChange={(e) => f.set("vin", e.target.value)} maxLength={17} /></Field>
          <Field label="Год выпуска" error={f.errors.year}><input className="input" inputMode="numeric" value={f.values.year} onChange={(e) => f.set("year", e.target.value)} /></Field>
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} onChange={(e) => f.set("note", e.target.value)} /></Field>
        </div>
        <div className="muted small">Пробег и моточасы здесь не правятся: они считаются из журнала показаний.</div>
      </div>
    </Modal>
  );
}

function AddReading({ v, onClose }: { v: Detail; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ kind: v.type.primary_reading, value: "", recorded_at: "", note: "" });
  const m = useMutation({
    mutationFn: () => post<{ review_state: string }>(`/vehicles/${v.id}/readings`, { kind: f.values.kind, value: numOrNull(f.values.value), recorded_at: f.values.recorded_at ? new Date(f.values.recorded_at).toISOString() : null, note: f.values.note }),
    onSuccess: (r) => {
      qc.invalidateQueries();
      if (r.review_state === "pending") toast.success("Записано, но скачок большой: показание ждёт проверки и пока не учитывается");
      else toast.success("Показание записано");
      onClose();
    },
    onError: f.fail,
  });
  const cur = f.values.kind === "km" ? v.current_km : v.current_hours;
  return (
    <Modal title="Ввести показание" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={!f.values.value} onClick={() => { f.clear(); m.mutate(); }}>Записать</Button></>}>
      <div className="stack">
        {f.formError && !f.errors.value && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          <Field label="Что вводим">
            <select className="input" value={f.values.kind} onChange={(e) => f.set("kind", e.target.value)}>
              <option value="km">Пробег, км</option><option value="engine_hours">Моточасы, м·ч</option>
            </select>
          </Field>
          <Field label="Значение" error={f.errors.value} hint={cur !== null ? `Сейчас: ${fmtNum(cur)} ${readingUnit(f.values.kind)}. Меньше нельзя.` : "Показаний ещё нет"}>
            <input className="input" inputMode="decimal" value={f.values.value} onChange={(e) => f.set("value", e.target.value)} aria-invalid={!!f.errors.value} />
          </Field>
          <Field label="Когда снято (по умолчанию — сейчас)" error={f.errors.recorded_at} full><input className="input" type="datetime-local" max={`${todayIso()}T23:59`} value={f.values.recorded_at} onChange={(e) => f.set("recorded_at", e.target.value)} /></Field>
          <Field label="Комментарий" full><input className="input" value={f.values.note} onChange={(e) => f.set("note", e.target.value)} maxLength={300} /></Field>
        </div>
        <div className="muted small">Введённое показание нельзя стереть или переписать: ошибку исправляют отдельной записью с причиной, и обе остаются в журнале.</div>
      </div>
    </Modal>
  );
}

function CorrectReading({ r, onClose }: { r: Reading; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ value: String(r.value), reason: "" });
  const m = useMutation({
    mutationFn: () => post(`/readings/${r.id}/correct`, { value: numOrNull(f.values.value), reason: f.values.reason }),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Исправление записано, прежнее значение осталось в журнале"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Исправить показание" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={!f.values.reason.trim()} onClick={() => { f.clear(); m.mutate(); }}>Исправить</Button></>}>
      <div className="stack">
        <div className="muted">Было: <strong style={{ color: "var(--text)" }}>{fmtNum(r.value)} {readingUnit(r.kind)}</strong> от {fmtDateTime(r.recorded_at)}</div>
        {f.formError && !f.errors.value && !f.errors.reason && <Alert tone="error">{f.formError}</Alert>}
        <Field label="Правильное значение" error={f.errors.value}><input className="input" inputMode="decimal" value={f.values.value} onChange={(e) => f.set("value", e.target.value)} /></Field>
        <Field label="Причина (обязательно)" error={f.errors.reason}><textarea className="input" value={f.values.reason} onChange={(e) => f.set("reason", e.target.value)} maxLength={300} /></Field>
      </div>
    </Modal>
  );
}

export function ReviewButtons({ r, onDone }: { r: { id: number }; onDone?: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reject, setReject] = useState(false);
  const decide = useMutation({
    mutationFn: (p: { decision: string; comment: string }) => post(`/readings/${r.id}/review`, p),
    onSuccess: (_d, p) => { qc.invalidateQueries(); toast.success(p.decision === "confirm" ? "Показание подтверждено" : "Показание отклонено"); onDone?.(); },
    onError: (e: any) => toast.error(e.message),
  });
  return (
    <>
      <Button size="sm" icon={CheckCircle2} loading={decide.isPending} onClick={() => decide.mutate({ decision: "confirm", comment: "" })}>Подтвердить</Button>
      <Button size="sm" variant="danger" icon={XCircle} onClick={() => setReject(true)}>Отклонить</Button>
      {reject && <ConfirmModal title="Отклонить показание" danger confirmLabel="Отклонить" reasonLabel="Почему показание неверное"
        text="Отклонённое показание остаётся в журнале, но в пробег машины не попадает."
        onConfirm={(reason) => decide.mutateAsync({ decision: "reject", comment: reason })} onClose={() => setReject(false)} />}
    </>
  );
}

function ReadingsTab({ v }: { v: Detail }) {
  const [adding, setAdding] = useState(false);
  const [fixing, setFixing] = useState<Reading | null>(null);
  const q = useQuery({ queryKey: ["readings", v.id], queryFn: () => get<Page<Reading>>(`/vehicles/${v.id}/readings`, { page_size: 100 }) });
  return (
    <Card flush title="Журнал показаний" actions={v.actions.add_reading && <Button variant="primary" size="sm" icon={Plus} onClick={() => setAdding(true)}>Ввести показание</Button>}>
      <QueryState q={q}>
        {q.data && (q.data.items.length === 0 ? <Empty icon={Gauge} title="Показаний пока нет">Введите первое: от него будет считаться пробег.</Empty> : (
          <Table rows={q.data.items} rowKey={(r) => r.id}
            columns={[
              { key: "at", header: "Когда", render: (r) => <span className="nowrap">{fmtDateTime(r.recorded_at)}</span> },
              { key: "kind", header: "Что", render: (r) => <span className="nowrap">{r.kind_label}</span> },
              { key: "val", header: "Значение", num: true, render: (r) => <strong className="nowrap">{fmtNum(r.value)} {readingUnit(r.kind)}</strong> },
              { key: "state", header: "Состояние", render: (r) => r.review_state === "pending" ? <Badge tone="warning">Ждёт проверки</Badge> : r.review_state === "rejected" ? <Badge tone="error">Отклонено</Badge> :
                r.superseded_by ? <Badge>Заменено исправлением</Badge> : r.corrects_id ? <Badge tone="mint">Исправление</Badge> : <Badge tone="success">Действует</Badge> },
              { key: "src", header: "Источник", hideSm: true, render: (r) => <>{r.source_label}{r.waybill_id && <> · <Link to={`/waybills/${r.waybill_id}`}>лист</Link></>}</> },
              { key: "who", header: "Кто", hideSm: true, render: (r) => r.author ?? "—" },
              { key: "note", header: "Комментарий", hideSm: true, render: (r) => <span className="muted">{r.review_comment || r.note}</span> },
              { key: "act", header: "", render: (r) => <div className="row">
                {r.effective && v.actions.correct_reading && <Button size="sm" icon={Pencil} onClick={() => setFixing(r)}>Исправить</Button>}
                {r.review_state === "pending" && v.actions.review_reading && <ReviewButtons r={r} />}
              </div> },
            ]} />
        ))}
      </QueryState>
      {adding && <AddReading v={v} onClose={() => setAdding(false)} />}
      {fixing && <CorrectReading r={fixing} onClose={() => setFixing(null)} />}
    </Card>
  );
}

function HistoryTab({ v }: { v: Detail }) {
  const q = useQuery({ queryKey: ["status-history", v.id], queryFn: () => get<{ items: Hist[] }>(`/vehicles/${v.id}/status-history`) });
  return (
    <Card title="История статусов">
      <QueryState q={q}>
        <ul className="timeline">
          {q.data?.items.map((h) => (
            <li key={h.id}>
              <span className="dot mint"><History size={12} aria-hidden /></span>
              <div>
                <div className="row wrap">
                  {h.from_label && <><span className="muted">{h.from_label}</span><span className="muted">→</span></>}
                  <VehicleStatus code={h.status} label={h.status_label} />
                  {h.request_id && <Link to={`/requests/${h.request_id}`}>обращение №{h.request_number}</Link>}
                </div>
                {h.reason && <div style={{ marginTop: 4 }}>{h.reason}</div>}
                <div className="muted small">{fmtDateTime(h.changed_at)} · {h.by ?? "система"}</div>
              </div>
            </li>
          ))}
        </ul>
      </QueryState>
    </Card>
  );
}

function AssignModal({ v, onClose }: { v: Detail; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const drivers = useQuery({ queryKey: ["drivers", v.legal_entity_id], queryFn: () => get<{ items: { id: number; full_name: string }[] }>("/employees/lookup", { drivers: true, legal_entity_id: v.legal_entity_id }) });
  const f = useForm({ employee_id: "", role: "primary", started_on: todayIso() });
  const m = useMutation({
    mutationFn: () => post(`/vehicles/${v.id}/assignments`, { employee_id: Number(f.values.employee_id), role: f.values.role, started_on: f.values.started_on || null }),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Водитель закреплён"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title="Закрепить водителя" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={!f.values.employee_id} onClick={() => { f.clear(); m.mutate(); }}>Закрепить</Button></>}>
      <div className="stack">
        {f.formError && !f.errors.employee_id && <Alert tone="error">{f.formError}</Alert>}
        <Field label="Водитель" error={f.errors.employee_id} hint="В списке только водители этого юрлица">
          <select className="input" value={f.values.employee_id} onChange={(e) => f.set("employee_id", e.target.value)}>
            <option value="">Выберите…</option>
            {drivers.data?.items.map((d) => <option key={d.id} value={d.id}>{d.full_name}</option>)}
          </select>
        </Field>
        <div className="form-grid">
          <Field label="Роль"><select className="input" value={f.values.role} onChange={(e) => f.set("role", e.target.value)}><option value="primary">Основной</option><option value="backup">Сменный</option></select></Field>
          <Field label="С какой даты" error={f.errors.started_on}><input className="input" type="date" value={f.values.started_on} onChange={(e) => f.set("started_on", e.target.value)} /></Field>
        </div>
      </div>
    </Modal>
  );
}

function DriversTab({ v }: { v: Detail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const q = useQuery({ queryKey: ["assignments", v.id], queryFn: () => get<{ items: Assign[] }>(`/vehicles/${v.id}/assignments`) });
  const end = useMutation({
    mutationFn: (id: number) => post(`/vehicles/${v.id}/assignments/${id}/end`, {}),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Закрепление снято"); },
    onError: (e: any) => toast.error(e.message),
  });
  return (
    <Card flush title="Закреплённые водители" actions={v.actions.assign && <Button size="sm" variant="primary" icon={UserPlus} onClick={() => setAdding(true)}>Закрепить водителя</Button>}>
      <QueryState q={q}>
        {q.data && (q.data.items.length === 0 ? <Empty title="За машиной никто не закреплён" /> : (
          <Table rows={q.data.items} rowKey={(a) => a.id} dim={(a) => !a.active}
            columns={[
              { key: "e", header: "Водитель", render: (a) => <strong>{a.employee}</strong> },
              { key: "r", header: "Роль", render: (a) => (a.role === "primary" ? "Основной" : "Сменный") },
              { key: "s", header: "С", render: (a) => fmtDate(a.started_on) },
              { key: "d", header: "По", render: (a) => (a.ended_on ? fmtDate(a.ended_on) : <span className="muted">по настоящее время</span>) },
              { key: "st", header: "", render: (a) => (a.active ? <Badge tone="success">Закреплён</Badge> : <Badge>Снято</Badge>) },
              { key: "x", header: "", render: (a) => a.active && v.actions.assign ? <Button size="sm" onClick={() => end.mutate(a.id)}>Снять</Button> : null },
            ]} />
        ))}
      </QueryState>
      {adding && <AssignModal v={v} onClose={() => setAdding(false)} />}
    </Card>
  );
}

export function VehicleCard() {
  const { id } = useParams();
  const vid = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<TabId>("overview");
  const [modal, setModal] = useState<"status" | "edit" | "archive" | null>(null);
  const q = useQuery({ queryKey: ["vehicle", vid], enabled: Number.isInteger(vid), queryFn: () => get<Detail>(`/vehicles/${vid}`) });
  const v = q.data;
  const archive = useMutation({
    mutationFn: (archived: boolean) => post(`/vehicles/${vid}/${archived ? "restore" : "archive"}`, {}),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Готово"); },
    onError: (e: any) => toast.error(e.message),
  });

  if (q.isLoading) return <Spinner />;
  if (!Number.isInteger(vid)) return <NotFound what="Машина" />;
  if (q.error || !v) return <QueryState q={q} what="Машина"><></></QueryState>;

  return (
    <>
      <PageHeader crumb={<Link to="/vehicles"><ArrowLeft size={13} aria-hidden /> Транспорт</Link>}
        title={<span className="row wrap" style={{ gap: 12 }}>{v.plate}<VehicleStatus code={v.status} label={v.status_label} />{v.archived && <Badge>В архиве</Badge>}</span>}
        sub={`${v.make} ${v.model}`.trim() + (v.legal_entity ? ` · ${v.legal_entity}` : "")}
        actions={<>
          {v.actions.create_request && <Link className="btn" to={`/requests?new=1&vehicle=${v.id}`}><Wrench size={16} aria-hidden /> Обращение</Link>}
          {v.actions.change_status && <Button onClick={() => setModal("status")}>Сменить статус</Button>}
          {v.actions.edit && <Button icon={Pencil} onClick={() => setModal("edit")}>Изменить</Button>}
          {v.actions.archive && (v.archived
            ? <Button icon={RotateCcw} onClick={() => archive.mutate(true)}>Вернуть из архива</Button>
            : <Button variant="danger" icon={Archive} onClick={() => setModal("archive")}>В архив</Button>)}
        </>} />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "overview", label: "Обзор" }, { id: "readings", label: "Показания" }, { id: "history", label: "История статусов" },
        { id: "drivers", label: "Водители" }, { id: "requests", label: "Обращения" }, { id: "waybills", label: "Путевые листы" },
      ]} />
      {tab === "overview" && (
        <div className="grid cols-2">
          <Card title="Данные">
            <dl className="kv">
              <dt>Тип</dt><dd>{v.type.name}</dd>
              <dt>Марка, модель</dt><dd>{`${v.make} ${v.model}`.trim() || "—"}</dd>
              <dt>Внутренний номер</dt><dd>{v.internal_no || "—"}</dd>
              <dt>VIN</dt><dd className="mono">{v.vin || "—"}</dd>
              <dt>Год выпуска</dt><dd>{v.year ?? "—"}</dd>
              <dt>Юрлицо</dt><dd>{v.legal_entity}</dd>
              <dt>Заметка</dt><dd>{v.note || <span className="muted">нет</span>}</dd>
            </dl>
          </Card>
          <Card title="Состояние">
            <dl className="kv">
              <dt>Статус</dt><dd><VehicleStatus code={v.status} label={v.status_label} /> <span className="muted small">с {fmtDateTime(v.status_changed_at)}</span></dd>
              <dt>Пробег</dt><dd><strong>{v.current_km !== null ? `${fmtNum(v.current_km)} км` : "—"}</strong></dd>
              <dt>Моточасы</dt><dd><strong>{v.current_hours !== null ? `${fmtNum(v.current_hours)} м·ч` : "—"}</strong></dd>
              <dt>Водитель</dt><dd>{v.driver?.name ?? <span className="muted">не закреплён</span>}</dd>
            </dl>
            <div className="muted small" style={{ marginTop: 12 }}>Пробег и моточасы считаются из журнала показаний и не правятся вручную. Основное показание типа: {reading(v)}.</div>
          </Card>
        </div>
      )}
      {tab === "readings" && <ReadingsTab v={v} />}
      {tab === "history" && <HistoryTab v={v} />}
      {tab === "drivers" && <DriversTab v={v} />}
      {tab === "requests" && <Card flush title="Обращения по этой машине"><RequestsTable vehicleId={v.id} /></Card>}
      {tab === "waybills" && <Card flush title="Путевые листы по этой машине"><WaybillsTable vehicleId={v.id} /></Card>}
      {modal === "status" && <StatusModal v={v} onClose={() => setModal(null)} />}
      {modal === "edit" && <EditModal v={v} onClose={() => setModal(null)} />}
      {modal === "archive" && <ConfirmModal title="Отправить в архив?" danger confirmLabel="В архив" text="Машина пропадёт из обычных списков, но вся история сохранится. Из архива её можно вернуть."
        onConfirm={async () => { await archive.mutateAsync(false); nav("/vehicles"); }} onClose={() => setModal(null)} />}
    </>
  );
}
