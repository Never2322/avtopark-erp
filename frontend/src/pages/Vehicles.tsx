import { FormEvent, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Car, Construction, Container, Download, Plus, Search, Truck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { get, post, qs } from "../api";
import { useAuth } from "../auth";
import { Page, useDebounced, useEntityParam, useUrlState, useVehicleTypes } from "../hooks";
import { fmtDate, fmtNum, todayIso } from "../format";
import { Alert, Button, Card, Empty, Field, FilterChips, Modal, numOrNull, PageHeader, Pagination, QueryState, Table, useForm, useToast, VehicleStatus } from "../ui";

export interface Insurance { kind: string; label: string; until: string }

export interface VehicleRow {
  id: number; legal_entity_id: number; legal_entity: string; plate: string; internal_no: string; make: string; model: string;
  vin: string | null; year: number | null; status: string; status_label: string; current_km: number | null; current_hours: number | null;
  archived: boolean; driver: { id: number; name: string } | null; next_service?: string | null; insurance?: Insurance[] | null;
  type: { id: number; name: string; category: string; primary_reading: string };
}

interface VehiclePage extends Page<VehicleRow> { facets: { categories: Record<string, number>; statuses: Record<string, number> } }

const CATEGORY_PLURAL: Record<string, string> = { truck: "Грузовые", special: "Спецтехника", car: "Легковые", trailer: "Прицепы", other: "Прочее" };
const CATEGORY_ICON: Record<string, LucideIcon> = { truck: Truck, special: Construction, car: Car, trailer: Container, other: Truck };

/** Срок ближайшего ТО: просрочен — красным, ближе двух недель — жёлтым. */
function NextService({ day }: { day?: string | null }) {
  if (!day) return <span className="muted">—</span>;
  const left = Math.round((Date.parse(day) - Date.parse(todayIso())) / 86_400_000);
  return <span className={left < 0 ? "due-bad" : left <= 14 ? "due-soon" : ""} title={left < 0 ? "Срок прошёл" : left <= 14 ? "Скоро" : undefined}>{fmtDate(day)}</span>;
}

/** Конец страховки (ОСАГО, КАСКО) из личного дела техники: показана та, что кончается раньше. Просрочена — красным, меньше 30 дней — жёлтым. */
function InsuranceUntil({ items }: { items?: Insurance[] | null }) {
  if (!items || items.length === 0) return <span className="muted" title="Страховка не внесена в личное дело техники">—</span>;
  const first = items[0];
  const left = Math.round((Date.parse(first.until) - Date.parse(todayIso())) / 86_400_000);
  const all = items.map((i) => `${i.label} до ${fmtDate(i.until)}`).join("; ");
  return (
    <span title={all}>
      <span className={left < 0 ? "due-bad" : left <= 30 ? "due-soon" : ""}>{fmtDate(first.until)}</span> <span className="muted">{first.label}</span>
    </span>
  );
}

export function reading(v: Pick<VehicleRow, "current_km" | "current_hours" | "type">): string {
  const km = v.current_km !== null ? `${fmtNum(v.current_km)} км` : null;
  const h = v.current_hours !== null ? `${fmtNum(v.current_hours)} м·ч` : null;
  const parts = v.type.primary_reading === "engine_hours" ? [h, km] : [km, h];
  return parts.filter(Boolean).join(" · ") || "—";
}

function CreateVehicle({ onClose }: { onClose: () => void }) {
  const { me, can, entityId } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const types = useVehicleTypes();
  const entities = me!.legal_entities.filter((e) => can("vehicles.create", e.id));
  // юрлицо по умолчанию: выбранное в переключателе вверху
  const f = useForm({ legal_entity_id: String((entities.find((x) => x.id === entityId) ?? entities[0])?.id ?? ""), type_id: "", plate: "", make: "", model: "", internal_no: "", vin: "", year: "", initial_km: "", initial_hours: "", note: "" });
  const m = useMutation({
    mutationFn: () => post<{ id: number }>("/vehicles", {
      legal_entity_id: Number(f.values.legal_entity_id), type_id: Number(f.values.type_id), plate: f.values.plate, make: f.values.make, model: f.values.model,
      internal_no: f.values.internal_no, vin: f.values.vin, year: numOrNull(f.values.year), initial_km: numOrNull(f.values.initial_km),
      initial_hours: numOrNull(f.values.initial_hours), note: f.values.note,
    }),
    onSuccess: (v) => { qc.invalidateQueries(); toast.success("Техника добавлена"); onClose(); nav(`/vehicles/${v.id}`); },
    onError: f.fail,
  });
  const submit = (e: FormEvent) => { e.preventDefault(); f.clear(); m.mutate(); };
  const t = types.data?.items.find((x) => String(x.id) === f.values.type_id);
  return (
    <Modal title="Добавить технику" onClose={onClose} wide footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" loading={m.isPending} onClick={submit} disabled={!f.values.plate || !f.values.type_id}>Добавить</Button>
    </>}>
      <form className="stack" onSubmit={submit}>
        {f.formError && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          {entities.length > 1 && (
            <Field label="Юридическое лицо" error={f.errors.legal_entity_id}>
              <select className="input" value={f.values.legal_entity_id} onChange={(e) => f.set("legal_entity_id", e.target.value)}>
                {entities.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
              </select>
            </Field>
          )}
          <Field label="Тип техники" error={f.errors.type_id}>
            <select className="input" value={f.values.type_id} onChange={(e) => f.set("type_id", e.target.value)} aria-invalid={!!f.errors.type_id}>
              <option value="">Выберите…</option>
              {types.data?.items.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
          </Field>
          <Field label="Госномер" error={f.errors.plate} hint="Регистр и пробелы не важны">
            <input className="input" value={f.values.plate} onChange={(e) => f.set("plate", e.target.value)} aria-invalid={!!f.errors.plate} maxLength={20} />
          </Field>
          <Field label="Внутренний номер" error={f.errors.internal_no}><input className="input" value={f.values.internal_no} onChange={(e) => f.set("internal_no", e.target.value)} maxLength={30} /></Field>
          <Field label="Марка" error={f.errors.make}><input className="input" value={f.values.make} onChange={(e) => f.set("make", e.target.value)} /></Field>
          <Field label="Модель" error={f.errors.model}><input className="input" value={f.values.model} onChange={(e) => f.set("model", e.target.value)} /></Field>
          <Field label="VIN" error={f.errors.vin} hint="17 символов, латиница и цифры"><input className="input" value={f.values.vin} onChange={(e) => f.set("vin", e.target.value)} maxLength={17} /></Field>
          <Field label="Год выпуска" error={f.errors.year}><input className="input" inputMode="numeric" value={f.values.year} onChange={(e) => f.set("year", e.target.value)} /></Field>
          <Field label="Пробег сейчас, км" error={f.errors.initial_km} hint={t?.primary_reading === "km" ? "Основное показание этого типа" : "Если известен"}>
            <input className="input" inputMode="decimal" value={f.values.initial_km} onChange={(e) => f.set("initial_km", e.target.value)} />
          </Field>
          <Field label="Моточасы сейчас, м·ч" error={f.errors.initial_hours} hint={t?.primary_reading === "engine_hours" ? "Основное показание этого типа" : "Если известны"}>
            <input className="input" inputMode="decimal" value={f.values.initial_hours} onChange={(e) => f.set("initial_hours", e.target.value)} />
          </Field>
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} onChange={(e) => f.set("note", e.target.value)} /></Field>
        </div>
      </form>
    </Modal>
  );
}

export function Vehicles() {
  const { can, me } = useAuth();
  const entity = useEntityParam();
  const [sp, setSp] = useSearchParams();
  const [s, setS] = useUrlState({ q: "", status: "", type_id: "", category: "", archived: "", sort: "plate", desc: "", page: "1" });
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  const [creating, setCreating] = useState(sp.get("new") === "1");
  const types = useVehicleTypes();
  const nav = useNavigate();

  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setText(s.q); }, [s.q]);
  useEffect(() => { if (sp.get("new") === "1") { setCreating(true); setSp((o) => { o.delete("new"); return o; }, { replace: true }); } }, [sp, setSp]);

  const params = { q: s.q, status: s.status, type_id: s.type_id, category: s.category, archived: s.archived === "1", legal_entity_id: entity, sort: s.sort, desc: s.desc === "1", page: Number(s.page), page_size: 25 };
  const list = useQuery({ queryKey: ["vehicles", params], queryFn: () => get<VehiclePage>("/vehicles", params), placeholderData: (p) => p });
  const multi = entity === undefined && me!.legal_entities.length > 1;       // колонка «Юрлицо» нужна, только если их несколько
  const onSort = (key: string) => setS({ sort: key, desc: s.sort === key && s.desc !== "1" ? "1" : "" });

  return (
    <>
      <PageHeader title="Транспорт" sub={`Управление автопарком и спецтехникой${list.data ? ` · показано: ${list.data.total}` : ""}`}
        actions={<>
          {can("vehicles.export") && <a className="btn" href={`/api/vehicles/export.csv${qs({ q: s.q, status: s.status, type_id: s.type_id, archived: s.archived === "1", legal_entity_id: entity })}`}><Download size={16} aria-hidden /> Выгрузить в Excel</a>}
          {can("vehicles.create") && <Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>Добавить технику</Button>}
        </>} />
      <Card flush>
        {list.data && (
          <div style={{ padding: "14px 14px 0" }}>
            <FilterChips value={s.category}
              onChange={(v) => setS({ category: v })}
              options={[{ id: "", label: "Все", count: Object.values(list.data.facets.categories).reduce((a, b) => a + b, 0) },
                ...Object.keys(CATEGORY_PLURAL).filter((code) => list.data.facets.categories[code]).map((code) => ({ id: code, label: CATEGORY_PLURAL[code], count: list.data.facets.categories[code] }))]} />
          </div>
        )}
        <div className="toolbar" style={{ padding: "14px 14px 0" }}>
          <div style={{ position: "relative", minWidth: 240, flex: "1 1 240px", maxWidth: 360 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Госномер, марка, VIN" aria-label="Поиск по технике" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <select className="input compact" aria-label="Тип техники" value={s.type_id} onChange={(e) => setS({ type_id: e.target.value })}>
            <option value="">Все типы</option>
            {types.data?.items.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <label className="check"><input type="checkbox" checked={s.archived === "1"} onChange={(e) => setS({ archived: e.target.checked ? "1" : "" })} /> Архив</label>
        </div>
        <div style={{ padding: "12px 14px" }}>
          <FilterChips value={s.status} onChange={(v) => setS({ status: v })}
            options={[{ id: "", label: "Все", count: list.data ? Object.values(list.data.facets.statuses).reduce((a, b) => a + b, 0) : undefined },
              ...(types.data?.statuses.map((x) => ({ id: x.code, label: x.label, count: list.data?.facets.statuses[x.code] ?? 0 })) ?? [])]} />
        </div>
        <QueryState q={list}>
          {list.data && (list.data.items.length === 0 ? (
            <Empty icon={Truck} title={s.q || s.status || s.type_id || s.category ? "Ничего не найдено" : "Техники пока нет"}>
              {s.q || s.status || s.type_id || s.category ? <Button onClick={() => { setText(""); setS({ q: "", status: "", type_id: "", category: "" }); }}>Сбросить фильтры</Button> : can("vehicles.create") ? "Добавьте первую машину кнопкой выше." : "Вам пока не открыта ни одна машина."}
            </Empty>
          ) : (
            <>
              <Table rows={list.data.items} rowKey={(v) => v.id} onRowClick={(v) => nav(`/vehicles/${v.id}`)} sort={{ key: s.sort, desc: s.desc === "1" }} onSort={onSort} dim={(v) => v.archived}
                columns={[
                  { key: "plate", header: "Госномер", sortKey: "plate", render: (v) => <span className="plate">{v.plate}</span> },
                  { key: "name", header: "Техника", sortKey: "make", render: (v) => <>{`${v.make} ${v.model}`.trim() || "—"}{v.internal_no && <span className="muted"> · №{v.internal_no}</span>}</> },
                  { key: "type", header: "Тип", hideSm: true, render: (v) => { const I = CATEGORY_ICON[v.type.category] ?? Truck; return <span className="type-cell"><I size={16} aria-hidden />{v.type.name}</span>; } },
                  { key: "status", header: "Статус", sortKey: "status", render: (v) => <VehicleStatus code={v.status} label={v.status_label} /> },
                  { key: "reading", header: "Пробег / моточасы", num: true, sortKey: s.sort === "current_hours" ? "current_hours" : "current_km", render: (v) => reading(v) },
                  { key: "driver", header: "Водитель", hideSm: true, render: (v) => v.driver?.name ?? <span className="muted">не закреплён</span> },
                  ...(can("vehicle_files.view") ? [{ key: "ins", header: "Страховка до", hideSm: true, render: (v: VehicleRow) => <InsuranceUntil items={v.insurance} /> }] : []),
                  ...(can("maintenance.view") ? [{ key: "next", header: "Следующее ТО", hideSm: true, render: (v: VehicleRow) => <NextService day={v.next_service} /> }] : []),
                  ...(multi ? [{ key: "entity", header: "Юрлицо", hideSm: true, render: (v: VehicleRow) => v.legal_entity }] : []),
                ]} />
              <Pagination page={list.data.page} pageSize={list.data.page_size} total={list.data.total} onPage={(p) => setS({ page: String(p) })} />
            </>
          ))}
        </QueryState>
      </Card>
      {creating && <CreateVehicle onClose={() => setCreating(false)} />}
    </>
  );
}
