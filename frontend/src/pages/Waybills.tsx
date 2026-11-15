import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Plus, Search } from "lucide-react";
import { get, post } from "../api";
import { useAuth } from "../auth";
import { fmtDate, fmtNum, todayIso } from "../format";
import { Page, useDebounced, useEntityParam, useUrlState } from "../hooks";
import { useDriverOptions, VehicleSelect } from "../components/pickers";
import { Alert, Button, Card, Empty, Field, FilterChips, Modal, PageHeader, Pagination, QueryState, Table, useForm, useToast, WaybillStatus } from "../ui";

export interface WaybillRow {
  id: number; number_label: string; legal_entity_id: number; status: string; status_label: string; work_date: string; shift: string; shift_label: string;
  vehicle: { id: number; plate: string; name: string }; driver: { id: number; name: string }; route: string; work_site: string;
  start_km: number | null; end_km: number | null; run_km: number | null; start_hours: number | null; end_hours: number | null; run_hours: number | null;
}
interface WaybillPage extends Page<WaybillRow> { counts: Record<string, number> }

export const SHIFTS: [string, string][] = [["day", "Дневная"], ["night", "Ночная"], ["full", "Сутки"]];

export function runText(w: Pick<WaybillRow, "run_km" | "run_hours" | "status">): string {
  if (w.status !== "closed") return "—";
  const parts = [w.run_km !== null ? `${fmtNum(w.run_km)} км` : null, w.run_hours !== null ? `${fmtNum(w.run_hours)} м·ч` : null].filter(Boolean);
  return parts.join(" · ") || "—";
}

function CreateWaybill({ vehicleId, onClose }: { vehicleId?: string; onClose: () => void }) {
  const { can } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const entity = useEntityParam();
  const chooseDriver = can("waybills.create");
  const f = useForm({ vehicle_id: vehicleId ?? "", driver_id: "", work_date: todayIso(), shift: "day", route: "", work_site: "", note: "" });
  const [entityOfVehicle, setEntityOfVehicle] = useState<number | undefined>(undefined);
  const drivers = useDriverOptions(entityOfVehicle ?? entity, chooseDriver);
  const m = useMutation({
    mutationFn: () => post<{ id: number }>("/waybills", { ...f.values, vehicle_id: Number(f.values.vehicle_id), driver_id: f.values.driver_id ? Number(f.values.driver_id) : null }),
    onSuccess: (w) => { qc.invalidateQueries(); toast.success("Путевой лист создан"); onClose(); nav(`/waybills/${w.id}`); },
    onError: f.fail,
  });
  return (
    <Modal title="Новый путевой лист" wide onClose={onClose} footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" loading={m.isPending} disabled={!f.values.vehicle_id || (chooseDriver && !f.values.driver_id)} onClick={() => { f.clear(); m.mutate(); }}>Создать черновик</Button>
    </>}>
      <div className="stack">
        {f.formError && !f.errors.vehicle_id && !f.errors.driver_id && !f.errors.work_date && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          <VehicleSelect value={f.values.vehicle_id} entityId={entity} error={f.errors.vehicle_id} onChange={(v, o) => { f.set("vehicle_id", v); setEntityOfVehicle(o?.legal_entity_id); f.set("driver_id", ""); }} />
          {chooseDriver ? (
            <Field label="Водитель" error={f.errors.driver_id} hint="Только водители, которые работают в юрлице этой машины">
              <select className="input" value={f.values.driver_id} aria-invalid={!!f.errors.driver_id} onChange={(e) => f.set("driver_id", e.target.value)}>
                <option value="">{drivers.isLoading ? "Загрузка…" : "Выберите водителя…"}</option>
                {drivers.data?.items.map((d) => <option key={d.id} value={d.id}>{d.full_name}</option>)}
              </select>
            </Field>
          ) : <Field label="Водитель"><input className="input" disabled value="Вы" readOnly /></Field>}
          <Field label="Дата работы" error={f.errors.work_date}><input className="input" type="date" value={f.values.work_date} onChange={(e) => f.set("work_date", e.target.value)} /></Field>
          <Field label="Смена" error={f.errors.shift}>
            <select className="input" value={f.values.shift} onChange={(e) => f.set("shift", e.target.value)}>{SHIFTS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}</select>
          </Field>
          <Field label="Маршрут" error={f.errors.route}><input className="input" value={f.values.route} maxLength={300} onChange={(e) => f.set("route", e.target.value)} /></Field>
          <Field label="Объект работ" error={f.errors.work_site}><input className="input" value={f.values.work_site} maxLength={200} onChange={(e) => f.set("work_site", e.target.value)} /></Field>
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} maxLength={2000} onChange={(e) => f.set("note", e.target.value)} /></Field>
        </div>
        <div className="muted small">Лист создаётся черновиком. Начальный пробег фиксируется, когда лист открывают.</div>
      </div>
    </Modal>
  );
}

function columns(showVehicle: boolean) {
  return [
    { key: "n", header: "Лист", render: (w: WaybillRow) => <strong>№{w.number_label}</strong> },
    { key: "d", header: "Дата", render: (w: WaybillRow) => <span className="nowrap">{fmtDate(w.work_date)}</span> },
    ...(showVehicle ? [{ key: "v", header: "Машина", render: (w: WaybillRow) => <><strong>{w.vehicle.plate}</strong><span className="muted"> {w.vehicle.name}</span></> }] : []),
    { key: "dr", header: "Водитель", hideSm: true, render: (w: WaybillRow) => w.driver.name },
    { key: "s", header: "Статус", render: (w: WaybillRow) => <WaybillStatus code={w.status} label={w.status_label} /> },
    { key: "r", header: "Пробег за смену", num: true, hideSm: true, render: (w: WaybillRow) => runText(w) },
  ];
}

export function WaybillsTable({ vehicleId }: { vehicleId: number }) {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["waybills", "vehicle", vehicleId], queryFn: () => get<WaybillPage>("/waybills", { vehicle_id: vehicleId, page_size: 100 }) });
  return (
    <QueryState q={q}>
      {q.data && (q.data.items.length === 0 ? <Empty icon={FileText} title="Путевых листов по этой машине нет" /> :
        <Table rows={q.data.items} rowKey={(w) => w.id} onRowClick={(w) => nav(`/waybills/${w.id}`)} columns={columns(false)} />)}
    </QueryState>
  );
}

export function Waybills() {
  const { can } = useAuth();
  const entity = useEntityParam();
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const [s, setS] = useUrlState({ q: "", status: "active", date_from: "", date_to: "", page: "1" });
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  const [creating, setCreating] = useState<null | { vehicle?: string }>(sp.get("new") === "1" ? { vehicle: sp.get("vehicle") ?? undefined } : null);

  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setText(s.q); }, [s.q]);
  useEffect(() => {
    if (sp.get("new") === "1") {
      setCreating({ vehicle: sp.get("vehicle") ?? undefined });
      setSp((o) => { o.delete("new"); o.delete("vehicle"); return o; }, { replace: true });
    }
  }, [sp, setSp]);

  const params = { q: s.q, status: s.status === "all" ? "" : s.status, date_from: s.date_from, date_to: s.date_to, legal_entity_id: entity, page: Number(s.page), page_size: 25 };
  const list = useQuery({ queryKey: ["waybills", params], queryFn: () => get<WaybillPage>("/waybills", params), placeholderData: (p) => p });
  const c = list.data?.counts;
  const filtered = !!(s.q || s.date_from || s.date_to || s.status !== "active");

  return (
    <>
      <PageHeader title="Путевые листы" sub={c ? `Сейчас в работе: ${c.open}` : undefined}
        actions={(can("waybills.create") || can("waybills.create_own")) && <Button variant="primary" icon={Plus} onClick={() => setCreating({})}>Новый лист</Button>} />
      <Card flush>
        <div className="toolbar" style={{ padding: "14px 14px 0" }}>
          <div style={{ position: "relative", minWidth: 240, flex: "1 1 240px", maxWidth: 360 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Номер, госномер, водитель, маршрут" aria-label="Поиск по путевым листам" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <label className="row" style={{ gap: 6 }}><span className="muted small">С</span><input className="input compact" type="date" aria-label="Дата с" value={s.date_from} onChange={(e) => setS({ date_from: e.target.value })} /></label>
          <label className="row" style={{ gap: 6 }}><span className="muted small">По</span><input className="input compact" type="date" aria-label="Дата по" value={s.date_to} onChange={(e) => setS({ date_to: e.target.value })} /></label>
        </div>
        <div style={{ padding: "12px 14px" }}>
          <FilterChips value={s.status} onChange={(v) => setS({ status: v })}
            options={[
              { id: "active", label: "Не закрыты", count: c ? c.draft + c.open : undefined }, { id: "draft", label: "Черновики", count: c?.draft },
              { id: "open", label: "Открыты", count: c?.open }, { id: "closed", label: "Закрыты", count: c?.closed },
              { id: "cancelled", label: "Аннулированы", count: c?.cancelled }, { id: "all", label: "Все" },
            ]} />
        </div>
        <QueryState q={list}>
          {list.data && (list.data.items.length === 0 ? (
            <Empty icon={FileText} title={filtered ? "Ничего не найдено" : "Незакрытых листов нет"}>
              {filtered ? <Button onClick={() => { setText(""); setS({ q: "", date_from: "", date_to: "", status: "active" }); }}>Сбросить фильтры</Button> : "Новый лист создаётся кнопкой выше."}
            </Empty>
          ) : (
            <>
              <Table rows={list.data.items} rowKey={(w) => w.id} onRowClick={(w) => nav(`/waybills/${w.id}`)} dim={(w) => w.status === "cancelled"} columns={columns(true)} />
              <Pagination page={list.data.page} pageSize={list.data.page_size} total={list.data.total} onPage={(p) => setS({ page: String(p) })} />
            </>
          ))}
        </QueryState>
      </Card>
      {creating && <CreateWaybill vehicleId={creating.vehicle} onClose={() => setCreating(null)} />}
    </>
  );
}
