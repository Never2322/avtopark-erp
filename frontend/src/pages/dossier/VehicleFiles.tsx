// «Личные дела техники»: список и карточка. В карточке всё о машине: фото, СТС, ПТС, страховки, характеристики и история.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ImagePlus, Pencil, Search, Truck } from "lucide-react";
import { get, put } from "../../api";
import { useAuth } from "../../auth";
import { fmtDate, fmtDateTime, fmtNum } from "../../format";
import { Page, useDebounced, useEntityParam, useUrlState } from "../../hooks";
import { Alert, Badge, Button, Card, Empty, Field, FilterChips, numOrNull, PageHeader, Pagination, QueryState, RequestStatus, Table, Tabs, useForm, useToast, VehicleStatus } from "../../ui";
import { Avatar, Checklist, Doc, DocsBlock, Kind, NewDocModal, Summary, SummaryBadges } from "./shared";

interface Row {
  id: number; plate: string; name: string; type: string; status: string; status_label: string; legal_entity: string; archived: boolean;
  photo_file_id: number | null; summary: Summary;
}

export function VehicleFiles() {
  const entity = useEntityParam();
  const nav = useNavigate();
  const [s, setS] = useUrlState({ q: "", problems: "", archived: "", page: "1" });
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setText(s.q); }, [s.q]);
  const params = { q: s.q, problems: s.problems === "1", archived: s.archived === "1", legal_entity_id: entity, page: Number(s.page), page_size: 25 };
  const list = useQuery({ queryKey: ["dossier-vehicles", params], queryFn: () => get<Page<Row>>("/dossier/vehicles", params), placeholderData: (p) => p });
  const multi = entity === undefined;
  return (
    <>
      <PageHeader title="Личные дела техники" sub="Всё на каждую машину: фото, СТС, ПТС, страховки, характеристики, история" />
      <Card flush>
        <div className="toolbar" style={{ padding: "14px 14px 0" }}>
          <div style={{ position: "relative", minWidth: 240, flex: "1 1 240px", maxWidth: 360 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Госномер, марка, VIN" aria-label="Поиск по технике" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <label className="check"><input type="checkbox" checked={s.archived === "1"} onChange={(e) => setS({ archived: e.target.checked ? "1" : "" })} /> Архив</label>
        </div>
        <div style={{ padding: "12px 14px" }}>
          <FilterChips value={s.problems} onChange={(v) => setS({ problems: v })} options={[{ id: "", label: "Вся техника" }, { id: "1", label: "Есть просрочка или скоро срок" }]} />
        </div>
        <QueryState q={list}>
          {list.data && (list.data.items.length === 0 ? (
            <Empty icon={Truck} title={s.q || s.problems ? "Ничего не найдено" : "Техники пока нет"}>{!s.q && !s.problems ? "Технику добавляют в разделе «Транспорт»." : undefined}</Empty>
          ) : (
            <>
              <Table rows={list.data.items} rowKey={(r) => r.id} onRowClick={(r) => nav(`/dossier/vehicles/${r.id}`)} dim={(r) => r.archived}
                columns={[
                  { key: "a", header: "", width: 64, render: (r) => <Avatar fileId={r.photo_file_id} kind="vehicle" size={44} /> },
                  { key: "p", header: "Техника", render: (r) => <><strong className="nowrap">{r.plate}</strong><div className="muted small">{r.name || r.type}</div></> },
                  { key: "t", header: "Тип", hideSm: true, render: (r) => r.type },
                  ...(multi ? [{ key: "e", header: "Юрлицо", hideSm: true, render: (r: Row) => r.legal_entity }] : []),
                  { key: "st", header: "Статус", hideSm: true, render: (r) => <VehicleStatus code={r.status} label={r.status_label} /> },
                  { key: "d", header: "Документов", num: true, hideSm: true, render: (r) => r.summary.documents },
                  { key: "s", header: "Состояние", render: (r) => <SummaryBadges s={r.summary} /> },
                ]} />
              <Pagination page={list.data.page} pageSize={list.data.page_size} total={list.data.total} onPage={(p) => setS({ page: String(p) })} />
            </>
          ))}
        </QueryState>
      </Card>
    </>
  );
}

/* ---------------- карточка ---------------- */

interface Specs {
  color: string; engine_no: string; chassis_no: string; body_no: string; fuel_type: string; eco_class: string; owner_name: string; ownership: string; note: string;
  engine_power_hp: number | null; engine_volume_cc: number | null; max_weight_kg: number | null; empty_weight_kg: number | null; tank_liters: number | null; fuel_norm: number | null;
  registered_on: string | null; commissioned_on: string | null;
}
interface Detail {
  vehicle: {
    id: number; plate: string; internal_no: string; make: string; model: string; vin: string | null; year: number | null; type: string; status: string; status_label: string;
    legal_entity: string; archived: boolean; current_km: number | null; current_hours: number | null; note: string;
  };
  specs: Specs; photo_file_id: number | null; can_edit: boolean; kinds: Kind[];
  options: { ownership: { code: string; label: string }[]; fuel_type: { code: string; label: string }[] };
  documents: Doc[]; archived_documents: Doc[]; summary: Summary;
  history: {
    drivers?: { name: string; role: string; started_on: string; ended_on: string | null }[];
    readings?: { kind: string; kind_label: string; value: number; recorded_at: string }[];
    statuses?: { status: string; status_label: string; reason: string; changed_at: string }[];
    requests?: { id: number; number: string; title: string; status: string; status_label: string }[];
  };
}

const text = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));

function SpecsForm({ d, onClose }: { d: Detail; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const sp = d.specs;
  const s = (v: number | null) => (v === null ? "" : String(v));
  const f = useForm({
    color: sp.color, engine_no: sp.engine_no, chassis_no: sp.chassis_no, body_no: sp.body_no, engine_power_hp: s(sp.engine_power_hp), engine_volume_cc: s(sp.engine_volume_cc),
    fuel_type: sp.fuel_type, max_weight_kg: s(sp.max_weight_kg), empty_weight_kg: s(sp.empty_weight_kg), tank_liters: s(sp.tank_liters), fuel_norm: s(sp.fuel_norm), eco_class: sp.eco_class, owner_name: sp.owner_name, ownership: sp.ownership,
    registered_on: sp.registered_on ?? "", commissioned_on: sp.commissioned_on ?? "", note: sp.note,
  });
  const m = useMutation({
    mutationFn: () => {
      const v = f.values;
      return put(`/dossier/vehicles/${d.vehicle.id}/specs`, {
        ...v, engine_power_hp: numOrNull(v.engine_power_hp, "engine_power_hp"), engine_volume_cc: numOrNull(v.engine_volume_cc, "engine_volume_cc"), max_weight_kg: numOrNull(v.max_weight_kg, "max_weight_kg"),
        empty_weight_kg: numOrNull(v.empty_weight_kg, "empty_weight_kg"), tank_liters: numOrNull(v.tank_liters, "tank_liters"), fuel_norm: numOrNull(v.fuel_norm, "fuel_norm"), registered_on: v.registered_on || null, commissioned_on: v.commissioned_on || null,
      });
    },
    onSuccess: () => { qc.invalidateQueries(); toast.success("Характеристики сохранены"); onClose(); },
    onError: f.fail,
  });
  return (
    <Card title="Характеристики: изменение">
      <div className="stack">
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          <Field label="Цвет" error={f.errors.color}><input className="input" value={f.values.color} maxLength={40} onChange={(e) => f.set("color", e.target.value)} /></Field>
          <Field label="Номер двигателя" error={f.errors.engine_no}><input className="input" value={f.values.engine_no} maxLength={40} onChange={(e) => f.set("engine_no", e.target.value)} /></Field>
          <Field label="Номер шасси (рамы)" error={f.errors.chassis_no}><input className="input" value={f.values.chassis_no} maxLength={40} onChange={(e) => f.set("chassis_no", e.target.value)} /></Field>
          <Field label="Номер кузова" error={f.errors.body_no}><input className="input" value={f.values.body_no} maxLength={40} onChange={(e) => f.set("body_no", e.target.value)} /></Field>
          <Field label="Мощность, л.с." error={f.errors.engine_power_hp}><input className="input" inputMode="decimal" value={f.values.engine_power_hp} onChange={(e) => f.set("engine_power_hp", e.target.value)} /></Field>
          <Field label="Объём двигателя, см³" error={f.errors.engine_volume_cc}><input className="input" inputMode="numeric" value={f.values.engine_volume_cc} onChange={(e) => f.set("engine_volume_cc", e.target.value)} /></Field>
          <Field label="Топливо" error={f.errors.fuel_type}>
            <select className="input" value={f.values.fuel_type} onChange={(e) => f.set("fuel_type", e.target.value)}><option value="">Не указано</option>{d.options.fuel_type.map((x) => <option key={x.code} value={x.code}>{x.label}</option>)}</select>
          </Field>
          <Field label="Экологический класс" error={f.errors.eco_class}><input className="input" value={f.values.eco_class} maxLength={10} onChange={(e) => f.set("eco_class", e.target.value)} /></Field>
          <Field label="Полная масса, кг" error={f.errors.max_weight_kg}><input className="input" inputMode="numeric" value={f.values.max_weight_kg} onChange={(e) => f.set("max_weight_kg", e.target.value)} /></Field>
          <Field label="Масса без нагрузки, кг" error={f.errors.empty_weight_kg}><input className="input" inputMode="numeric" value={f.values.empty_weight_kg} onChange={(e) => f.set("empty_weight_kg", e.target.value)} /></Field>
          <Field label="Объём бака, л" error={f.errors.tank_liters} hint="Нужен, чтобы заметить ошибку в литрах при заправке"><input className="input" inputMode="decimal" value={f.values.tank_liters} onChange={(e) => f.set("tank_liters", e.target.value)} /></Field>
          <Field label="Норма расхода, л на 100 км" error={f.errors.fuel_norm} hint="По ней программа отмечает перерасход топлива"><input className="input" inputMode="decimal" value={f.values.fuel_norm} onChange={(e) => f.set("fuel_norm", e.target.value)} /></Field>
          <Field label="Владелец" error={f.errors.owner_name}><input className="input" value={f.values.owner_name} maxLength={200} onChange={(e) => f.set("owner_name", e.target.value)} /></Field>
          <Field label="Форма владения" error={f.errors.ownership}>
            <select className="input" value={f.values.ownership} onChange={(e) => f.set("ownership", e.target.value)}><option value="">Не указана</option>{d.options.ownership.map((x) => <option key={x.code} value={x.code}>{x.label}</option>)}</select>
          </Field>
          <Field label="Поставлена на учёт" error={f.errors.registered_on}><input className="input" type="date" value={f.values.registered_on} onChange={(e) => f.set("registered_on", e.target.value)} /></Field>
          <Field label="В работе с" error={f.errors.commissioned_on}><input className="input" type="date" value={f.values.commissioned_on} onChange={(e) => f.set("commissioned_on", e.target.value)} /></Field>
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} maxLength={2000} onChange={(e) => f.set("note", e.target.value)} /></Field>
        </div>
        <div className="row wrap"><Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Сохранить</Button><Button onClick={onClose}>Отмена</Button></div>
      </div>
    </Card>
  );
}


function SpecsBlock({ d }: { d: Detail }) {
  const [edit, setEdit] = useState(false);
  const sp = d.specs;
  const v = d.vehicle;
  if (edit) return <SpecsForm d={d} onClose={() => setEdit(false)} />;
  const label = (list: { code: string; label: string }[], code: string) => list.find((x) => x.code === code)?.label ?? code;
  return (
      <Card title="Характеристики" actions={d.can_edit && <Button size="sm" icon={Pencil} onClick={() => setEdit(true)}>Изменить</Button>}>
        <div className="grid cols-2">
          <dl className="kv compact">
            <dt>Госномер</dt><dd>{v.plate}</dd>
            <dt>Внутренний номер</dt><dd>{text(v.internal_no)}</dd>
            <dt>Тип</dt><dd>{v.type}</dd>
            <dt>Марка, модель</dt><dd>{text(`${v.make} ${v.model}`.trim())}</dd>
            <dt>Год выпуска</dt><dd>{text(v.year)}</dd>
            <dt>VIN</dt><dd className="mono">{text(v.vin)}</dd>
            <dt>Цвет</dt><dd>{text(sp.color)}</dd>
            <dt>Номер двигателя</dt><dd className="mono">{text(sp.engine_no)}</dd>
            <dt>Номер шасси (рамы)</dt><dd className="mono">{text(sp.chassis_no)}</dd>
            <dt>Номер кузова</dt><dd className="mono">{text(sp.body_no)}</dd>
          </dl>
          <dl className="kv compact">
            <dt>Мощность</dt><dd>{sp.engine_power_hp === null ? "—" : `${fmtNum(sp.engine_power_hp)} л.с.`}</dd>
            <dt>Объём двигателя</dt><dd>{sp.engine_volume_cc === null ? "—" : `${fmtNum(sp.engine_volume_cc, 0)} см³`}</dd>
            <dt>Топливо</dt><dd>{sp.fuel_type ? label(d.options.fuel_type, sp.fuel_type) : "—"}</dd>
            <dt>Экологический класс</dt><dd>{text(sp.eco_class)}</dd>
            <dt>Полная масса</dt><dd>{sp.max_weight_kg === null ? "—" : `${fmtNum(sp.max_weight_kg, 0)} кг`}</dd>
            <dt>Масса без нагрузки</dt><dd>{sp.empty_weight_kg === null ? "—" : `${fmtNum(sp.empty_weight_kg, 0)} кг`}</dd>
            <dt>Объём бака</dt><dd>{sp.tank_liters === null ? "—" : `${fmtNum(sp.tank_liters)} л`}</dd>
            <dt>Норма расхода</dt><dd>{sp.fuel_norm === null ? "—" : `${fmtNum(sp.fuel_norm)} л на 100 км`}</dd>
            <dt>Владелец</dt><dd>{text(sp.owner_name)}</dd>
            <dt>Форма владения</dt><dd>{sp.ownership ? label(d.options.ownership, sp.ownership) : "—"}</dd>
            <dt>Поставлена на учёт</dt><dd>{fmtDate(sp.registered_on)}</dd>
            <dt>В работе с</dt><dd>{fmtDate(sp.commissioned_on)}</dd>
          </dl>
        </div>
        {sp.note && <div style={{ whiteSpace: "pre-wrap", marginTop: 12 }}>{sp.note}</div>}
        <p className="muted small" style={{ marginTop: 12 }}>Госномер, марка, тип, VIN и год меняются в разделе «Транспорт».</p>
      </Card>
    );
}

function HistoryBlock({ d }: { d: Detail }) {
  const h = d.history;
  const { can } = useAuth();
  if (!h.drivers && !h.requests) return <Alert tone="info">История из других разделов вам не показывается: для неё нужны права на «Транспорт» и «Обращения».</Alert>;
  return (
    <div className="stack">
      {h.drivers && (
        <Card title="Кто ездил" flush>
          {h.drivers.length === 0 ? <Empty title="Водители не закреплены" /> : (
            <Table rows={h.drivers} rowKey={(r) => r.name + r.started_on}
              columns={[
                { key: "n", header: "Водитель", render: (r) => <strong>{r.name}</strong> },
                { key: "r", header: "Роль", render: (r) => r.role || "—" },
                { key: "s", header: "С", render: (r) => fmtDate(r.started_on) },
                { key: "e", header: "По", render: (r) => (r.ended_on ? fmtDate(r.ended_on) : <Badge tone="success">сейчас</Badge>) },
              ]} />
          )}
        </Card>
      )}
      {h.readings && (
        <Card title="Последние показания" flush>
          {h.readings.length === 0 ? <Empty title="Показаний ещё нет" /> : (
            <Table rows={h.readings} rowKey={(r) => r.recorded_at + r.kind}
              columns={[
                { key: "t", header: "Когда", render: (r) => <span className="nowrap">{fmtDateTime(r.recorded_at)}</span> },
                { key: "k", header: "Что", render: (r) => r.kind_label },
                { key: "v", header: "Значение", num: true, render: (r) => <strong>{fmtNum(r.value)}</strong> },
              ]} />
          )}
        </Card>
      )}
      {h.statuses && h.statuses.length > 0 && (
        <Card title="Смены статуса">
          <ul className="timeline">
            {h.statuses.map((x, i) => (
              <li key={i}><span className="dot mint" aria-hidden>●</span><div><VehicleStatus code={x.status} label={x.status_label} /> <span className="muted small">{fmtDateTime(x.changed_at)}</span>{x.reason && <div className="small">{x.reason}</div>}</div></li>
            ))}
          </ul>
        </Card>
      )}
      {h.requests && (
        <Card title="Обращения и ремонты" flush>
          {h.requests.length === 0 ? <Empty title="Обращений нет" /> : (
            <Table rows={h.requests} rowKey={(r) => r.id}
              columns={[
                { key: "n", header: "№", render: (r) => (can("requests.view") ? <Link to={`/requests/${r.id}`}>{r.number}</Link> : r.number) },
                { key: "t", header: "Тема", render: (r) => r.title },
                { key: "s", header: "Статус", render: (r) => <RequestStatus code={r.status} label={r.status_label} /> },
              ]} />
          )}
        </Card>
      )}
    </div>
  );
}

export function VehicleFile() {
  const { id } = useParams();
  const { can } = useAuth();
  const [tab, setTab] = useState<"docs" | "specs" | "history">("docs");
  const [photo, setPhoto] = useState(false);
  const q = useQuery({ queryKey: ["dossier-vehicle", id], queryFn: () => get<Detail>(`/dossier/vehicles/${id}`) });
  const d = q.data;
  const v = d?.vehicle;
  return (
    <>
      <PageHeader crumb={<Link to="/dossier/vehicles"><ArrowLeft size={14} aria-hidden /> Личные дела техники</Link>}
        title={v ? `${v.plate} · ${`${v.make} ${v.model}`.trim() || v.type}` : "Личное дело техники"}
        sub={v ? [v.type, v.legal_entity].filter(Boolean).join(" · ") : undefined}
        actions={v && can("vehicles.view") ? <Link className="btn" to={`/vehicles/${v.id}`}>Карточка в «Транспорте»</Link> : undefined} />
      <QueryState q={q} what="Техника">
        {d && v && (
          <div className="stack">
            <Card>
              <div className="person-head">
                <div className="big-photo">
                  <Avatar fileId={d.photo_file_id} kind="vehicle" size={132} />
                  {d.can_edit && <Button size="sm" icon={ImagePlus} onClick={() => setPhoto(true)}>{d.photo_file_id ? "Заменить фото" : "Добавить фото"}</Button>}
                </div>
                <div className="stack-sm" style={{ flex: 1, minWidth: 0 }}>
                  <div className="row wrap"><VehicleStatus code={v.status} label={v.status_label} />{v.archived && <Badge>В архиве</Badge>}<SummaryBadges s={d.summary} /></div>
                  <dl className="kv compact">
                    <dt>Пробег</dt><dd>{[v.current_km !== null ? `${fmtNum(v.current_km)} км` : null, v.current_hours !== null ? `${fmtNum(v.current_hours)} м·ч` : null].filter(Boolean).join(" · ") || "—"}</dd>
                    <dt>VIN</dt><dd className="mono">{text(v.vin)}</dd>
                    <dt>Год выпуска</dt><dd>{text(v.year)}</dd>
                  </dl>
                  <Checklist kinds={d.kinds} docs={d.documents} />
                </div>
              </div>
            </Card>
            <Tabs value={tab} onChange={setTab} tabs={[{ id: "docs", label: `Документы (${d.summary.documents})` }, { id: "specs", label: "Характеристики" }, { id: "history", label: "История" }]} />
            {tab === "docs" && <Card><DocsBlock owner={{ type: "vehicles", id: v.id }} kinds={d.kinds} docs={d.documents} archived={d.archived_documents} canEdit={d.can_edit} /></Card>}
            {tab === "specs" && <SpecsBlock d={d} />}
            {tab === "history" && <HistoryBlock d={d} />}
          </div>
        )}
      </QueryState>
      {photo && d && v && <NewDocModal owner={{ type: "vehicles", id: v.id }} kinds={d.kinds} kind="photo" onClose={() => setPhoto(false)} />}
    </>
  );
}
