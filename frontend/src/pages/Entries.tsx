// Записи водителей: рейсы и заправки. «Мои записи» у водителя («/entries») и лента «Записи водителей» у диспетчера («/entries/feed»).
// Журналы рейсов и заправок со списком, правкой и выгрузкой — общие разделы (Registry); здесь внесение с телефона, фото и лента.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, ExternalLink, Fuel, Route as RouteIcon, Trash2, Truck, X } from "lucide-react";
import { ApiError, get, post, upload, errorText } from "../api";
import { shrinkAll } from "../images";
import { enqueue, isOffline, outboxAvailable } from "../outbox";
import { OutboxList } from "../components/Outbox";
import { fmtDate, fmtDateLong, fmtDateTime, fmtMoney, fmtNum, plural, todayIso } from "../format";
import { useEntityParam } from "../hooks";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, Modal, PageHeader, QueryState, Tone, numOrNull, useForm, useToast } from "../ui";

interface Pic { id: number; name: string; has_thumb: boolean }
export interface Entry {
  id: number; kind: "trip" | "fuel"; kind_label: string; date: string; vehicle_id: number; vehicle_id_label: string; driver_id_label: string;
  created_by: string; created_at: string; mine: boolean; legal_entity: string; note: string;
  route?: string; customer?: string; cargo?: string; cargo_kg?: number | null; doc_number?: string; km?: number | null;
  liters?: number; amount?: number; odometer?: number | null; full_tank?: boolean; station?: string; per_l?: number | null;
  consumption?: string; norm?: number | null; check?: { text: string; tone: Tone }; check_notes?: string[];
  pics: Pic[]; can: { photo: boolean; remove_photo: boolean; delete: boolean };
}
interface MyVehicle { id: number; label: string; legal_entity_id: number; tank_liters: number | null; fuel_norm: number | null }
export interface Mine { vehicles: MyVehicle[]; items: Entry[]; can: { trip: boolean; fuel: boolean }; today: string; max_photos: number; delete_hours: number }
interface Feed {
  items: Entry[]; truncated: boolean; date_from: string; date_to: string;
  totals: { trips: number; fills: number; liters: number; amount: number };
}

const photoUrl = (id: number, thumb = false) => `/api/entries/photos/${id}${thumb ? "?thumb=true" : ""}`;
const plateOf = (label: string) => label.split(" · ")[0];
const shift = (iso: string, days: number) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + days); return d.toISOString().slice(0, 10); };
const newId = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`);

/** Всё, что меняет запись, обновляет и страницу водителя, и ленту, и журналы. */
function useRefresh() {
  const qc = useQueryClient();
  return () => { for (const k of ["entries-mine", "entries-feed", "entry-one", "reg", "dashboard"]) qc.invalidateQueries({ queryKey: [k] }); };
}

/* ---------------- фото ---------------- */

function EntryPhotos({ e, onChanged, max = 3 }: { e: Entry; onChanged: () => void; max?: number }) {
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [view, setView] = useState<Pic | null>(null);
  const [removing, setRemoving] = useState<Pic | null>(null);
  const title = e.kind === "trip" ? "Накладная" : "Чек";
  const add = useMutation({
    mutationFn: async (files: File[]) => { const f = new FormData(); (await shrinkAll(files)).forEach((x) => f.append("files", x)); return upload(`/entries/${e.kind}/${e.id}/photos`, f); },
    onSuccess: onChanged,
    onError: (err) => toast.error(errorText(err)),
  });
  return (
    <div className="job-stage">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{title}</strong>
        <span className="muted small">{e.pics.length ? `${e.pics.length} из ${max}` : "нет фото"}</span>
      </div>
      {e.pics.length > 0 && (
        <div className="scan-grid job-thumbs">
          {e.pics.map((ph) => (
            <div className="scan" key={ph.id}>
              <button className="scan-open" onClick={() => setView(ph)} aria-label={`Открыть фото: ${ph.name}`}><img src={photoUrl(ph.id, true)} alt="" loading="lazy" /></button>
              {e.can.remove_photo && <button className="scan-x" onClick={() => setRemoving(ph)} aria-label="Убрать фото" title="Убрать фото"><X size={14} aria-hidden /></button>}
            </div>
          ))}
        </div>
      )}
      {e.can.photo && (
        <>
          <input ref={input} type="file" accept="image/*" multiple hidden onChange={(ev) => { const files = Array.from(ev.target.files ?? []); ev.target.value = ""; if (files.length) add.mutate(files); }} />
          <Button icon={Camera} loading={add.isPending} onClick={() => input.current?.click()}>Добавить фото</Button>
        </>
      )}
      {view && (
        <Modal title={`${title}: ${view.name}`} wide onClose={() => setView(null)}
          footer={<><a className="btn" href={photoUrl(view.id)} target="_blank" rel="noreferrer"><ExternalLink size={16} aria-hidden /> Открыть отдельно</a><Button variant="primary" onClick={() => setView(null)}>Закрыть</Button></>}>
          <div className="viewer"><img src={photoUrl(view.id)} alt={view.name} /></div>
        </Modal>
      )}
      {removing && (
        <ConfirmModal title="Убрать фото" danger confirmLabel="Убрать" reasonLabel="Причина" reasonRequired={false}
          text="Фото пропадёт из записи, но останется в журнале. Причину можно не писать."
          onConfirm={async (reason) => { await post(`/entries/photos/${removing.id}/remove`, { reason }); onChanged(); }} onClose={() => setRemoving(null)} />
      )}
    </div>
  );
}

/* ---------------- карточка записи ---------------- */

export function EntryCard({ e, onChanged, showWho }: { e: Entry; onChanged: () => void; showWho?: boolean }) {
  const toast = useToast();
  const [deleting, setDeleting] = useState(false);
  const problem = e.kind === "fuel" && (e.check_notes?.length ?? 0) > 0;
  return (
    <div className={`card job-card entry-card ${problem ? "late" : ""}`}>
      <div className="job-head">
        <span className="entry-ico" aria-hidden>{e.kind === "trip" ? <RouteIcon size={18} /> : <Fuel size={18} />}</span>
        <div className="job-title">
          <strong><Truck size={14} aria-hidden /> {plateOf(e.vehicle_id_label)}</strong>
          <span className="muted small">{e.kind_label} · {fmtDate(e.date)}{e.driver_id_label ? ` · ${e.driver_id_label}` : ""}</span>
        </div>
        {e.kind === "fuel" && e.check && e.check.text !== "—" && <Badge tone={e.check.tone}>{e.check.text}</Badge>}
      </div>
      <div className="job-body">
        {e.kind === "trip" ? (
          <>
            <div className="job-customer">{e.route}</div>
            {e.customer && <div>{e.customer}</div>}
            {(e.cargo || e.cargo_kg) && <div>{[e.cargo, e.cargo_kg ? `${fmtNum(e.cargo_kg, 0)} кг` : ""].filter(Boolean).join(" · ")}</div>}
            <div className="muted small">
              {[e.doc_number ? `Накладная № ${e.doc_number}` : "", e.km ? `${fmtNum(e.km, 0)} км` : ""].filter(Boolean).join(" · ")}
            </div>
          </>
        ) : (
          <>
            <div className="job-customer">{fmtNum(e.liters, 2)} л{e.amount ? ` · ${fmtMoney(e.amount)} ₽` : ""}{e.per_l ? <span className="muted small"> ({fmtNum(e.per_l, 2)} ₽/л)</span> : null}</div>
            <div className="muted small">
              {[e.odometer ? `Пробег ${fmtNum(e.odometer, 0)} км` : "", e.full_tank ? "до полного бака" : "не до полного", e.station].filter(Boolean).join(" · ")}
            </div>
            {e.consumption && <div>Расход: <strong>{e.consumption} л/100 км</strong>{e.norm ? <span className="muted small"> (норма {fmtNum(e.norm)})</span> : null}</div>}
            {e.check_notes?.map((n) => <Alert key={n} tone="warning">{n}</Alert>)}
          </>
        )}
        {e.note && <div className="job-note">{e.note}</div>}
        {showWho && e.created_by && <div className="muted small">Внёс: {e.created_by}, {fmtDateTime(e.created_at)}</div>}
      </div>
      {(e.pics.length > 0 || e.can.photo) && <div className="job-photos single"><EntryPhotos e={e} onChanged={onChanged} /></div>}
      {e.can.delete && (
        <div className="job-actions">
          <span className="spacer" />
          <Button variant="ghost" size="sm" icon={Trash2} onClick={() => setDeleting(true)}>Убрать запись</Button>
        </div>
      )}
      {deleting && (
        <ConfirmModal title="Убрать запись" danger confirmLabel="Убрать"
          text="Запись пропадёт из списка. Убрать свою запись можно только в первые сутки."
          onConfirm={async () => {
            const r = await post<{ note: string }>(`/entries/mine/${e.kind}/${e.id}/delete`);
            onChanged();
            toast.success(r.note || "Запись убрана");
          }} onClose={() => setDeleting(false)} />
      )}
    </div>
  );
}

/* ---------------- форма внесения ---------------- */

export function EntryModal({ kind, data, onClose, onDone }: { kind: "trip" | "fuel"; data: Mine; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const clientId = useRef(newId()).current;            // при повторной отправке (связь пропала) сервер не создаст вторую запись
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [warnings, setWarnings] = useState<string[] | null>(null);
  const [queued, setQueued] = useState(false);              // связи не было: запись лежит в телефоне и уйдёт сама
  const [general, setGeneral] = useState("");            // ошибка, которая не относится к одному полю (нет связи, нет прав)
  const f = useForm<Record<string, any>>({
    date: data.today, vehicle_id: String(data.vehicles[0]?.id ?? ""),
    route: "", customer: "", cargo: "", cargo_kg: "", doc_number: "", km: "",
    liters: "", amount: "", odometer: "", full_tank: true, station: "", note: "",
  });
  const urls = useMemo(() => files.map((x) => URL.createObjectURL(x)), [files]);
  useEffect(() => () => urls.forEach((u) => URL.revokeObjectURL(u)), [urls]);
  const vehicle = data.vehicles.find((v) => String(v.id) === String(f.values.vehicle_id));
  const isTrip = kind === "trip";

  const send = useMutation({
    mutationFn: async () => {
      const v = f.values;
      const body: Record<string, unknown> = isTrip
        ? { trip_date: v.date, vehicle_id: Number(v.vehicle_id), route: v.route, customer: v.customer, cargo: v.cargo, cargo_kg: numOrNull(String(v.cargo_kg), "cargo_kg"), doc_number: v.doc_number, km: numOrNull(String(v.km), "km"), note: v.note }
        : { fill_date: v.date, vehicle_id: Number(v.vehicle_id), liters: numOrNull(String(v.liters), "liters"), amount: numOrNull(String(v.amount), "amount") ?? 0, odometer: numOrNull(String(v.odometer), "odometer"), full_tank: !!v.full_tank, station: v.station, note: v.note };
      body.client_id = clientId;
      const form = new FormData();
      form.set("data", JSON.stringify(body));
      files.forEach((x) => form.append("files", x));
      try {
        return await upload<{ item: Entry; warnings: string[]; duplicate: boolean; queued?: undefined }>(`/entries/mine/${kind}`, form);
      } catch (e) {
        if (!isOffline(e) || !outboxAvailable()) throw e;
        const plate = vehicle?.label.split(" · ")[0] ?? "";
        const title = isTrip ? `Рейс · ${plate} · ${v.route}` : `Заправка · ${plate} · ${v.liters} л`;
        await enqueue({ kind, title, url: `/entries/mine/${kind}`, fields: { data: JSON.stringify(body) }, files: files.map((x) => ({ name: x.name, blob: x })) });
        return { queued: true as const };
      }
    },
    onSuccess: (r) => {
      onDone();
      if (r.queued) setQueued(true);
      else if (r.warnings.length) setWarnings(r.warnings);
      else { toast.success(isTrip ? "Рейс записан" : "Заправка записана"); onClose(); }
    },
    onError: (e) => { f.fail(e); setGeneral(e instanceof ApiError && (e.field || e.fields) ? "" : errorText(e)); },
  });

  const pick = async (chosen: File[]) => {            // файлы копируются сразу: после сброса поля выбора список браузера пустеет
    const room = Math.max(data.max_photos - files.length, 0);
    const small = await shrinkAll(chosen.slice(0, room));
    setFiles((old) => [...old, ...small].slice(0, data.max_photos));
  };

  if (queued) {
    return (
      <Modal title="Сохранено в телефоне" onClose={onClose} footer={<Button variant="primary" onClick={onClose}>Понятно</Button>}>
        <div className="stack-sm">
          <Alert tone="warning">Связи нет, поэтому {isTrip ? "рейс" : "заправка"} пока только в вашем телефоне.</Alert>
          <p>Как только сеть появится, запись уйдёт сама. Ничего повторно вносить не нужно. Список неотправленного виден наверху страницы «Мои записи».</p>
          <p className="muted small">Не закрывайте и не удаляйте программу на телефоне, пока запись не уйдёт.</p>
        </div>
      </Modal>
    );
  }
  if (warnings) {
    return (
      <Modal title="Записано" onClose={onClose} footer={<Button variant="primary" onClick={onClose}>Понятно</Button>}>
        <div className="stack-sm">
          <Alert tone="success">{isTrip ? "Рейс записан." : "Заправка записана."} Диспетчер её видит.</Alert>
          {warnings.map((w) => <Alert key={w} tone="warning">{w}</Alert>)}
          <p className="muted small">Если вы ошиблись, уберите запись в списке и внесите заново: это можно сделать в первые сутки.</p>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title={isTrip ? "Новый рейс" : "Новая заправка"} onClose={onClose}
      footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={send.isPending} onClick={() => { f.clear(); setGeneral(""); send.mutate(); }}>Записать</Button></>}>
      <div className="stack">
        {general && <Alert tone="error">{general}</Alert>}
        <div className="form-grid">
          <Field label="Дата" error={f.errors[isTrip ? "trip_date" : "fill_date"]}>
            <input className="input" type="date" value={f.values.date} max={data.today} onChange={(e) => f.set("date", e.target.value)} />
          </Field>
          {data.vehicles.length > 1 ? (
            <Field label="Техника" error={f.errors.vehicle_id}>
              <select className="input" value={f.values.vehicle_id} onChange={(e) => f.set("vehicle_id", e.target.value)}>
                {data.vehicles.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
              </select>
            </Field>
          ) : <div className="field"><span className="label">Техника</span><div className="input-static">{vehicle?.label}</div>{f.errors.vehicle_id && <span className="err" role="alert">{f.errors.vehicle_id}</span>}</div>}
          {isTrip ? (
            <>
              <Field label="Маршрут" full error={f.errors.route} hint="Откуда и куда, например «Склад — карьер Северный»">
                <input className="input" value={f.values.route} maxLength={200} onChange={(e) => f.set("route", e.target.value)} />
              </Field>
              <Field label="Заказчик" error={f.errors.customer}><input className="input" value={f.values.customer} maxLength={120} onChange={(e) => f.set("customer", e.target.value)} /></Field>
              <Field label="Груз" error={f.errors.cargo}><input className="input" value={f.values.cargo} maxLength={200} onChange={(e) => f.set("cargo", e.target.value)} /></Field>
              <Field label="Вес груза, кг" error={f.errors.cargo_kg}><input className="input" inputMode="decimal" value={f.values.cargo_kg} onChange={(e) => f.set("cargo_kg", e.target.value)} /></Field>
              <Field label="Номер накладной" error={f.errors.doc_number}><input className="input" value={f.values.doc_number} maxLength={60} onChange={(e) => f.set("doc_number", e.target.value)} /></Field>
              <Field label="Пройдено, км" error={f.errors.km}><input className="input" inputMode="numeric" value={f.values.km} onChange={(e) => f.set("km", e.target.value)} /></Field>
            </>
          ) : (
            <>
              <Field label="Литров" error={f.errors.liters} hint={vehicle?.tank_liters ? `Бак вмещает ${fmtNum(vehicle.tank_liters)} л` : undefined}>
                <input className="input" inputMode="decimal" value={f.values.liters} onChange={(e) => f.set("liters", e.target.value)} />
              </Field>
              <Field label="Сумма по чеку, ₽" error={f.errors.amount}><input className="input" inputMode="decimal" value={f.values.amount} onChange={(e) => f.set("amount", e.target.value)} /></Field>
              <Field label="Пробег по спидометру, км" error={f.errors.odometer} hint="По нему считается расход топлива">
                <input className="input" inputMode="numeric" value={f.values.odometer} onChange={(e) => f.set("odometer", e.target.value)} />
              </Field>
              <Field label="Заправка (АЗС)" error={f.errors.station}><input className="input" value={f.values.station} maxLength={80} onChange={(e) => f.set("station", e.target.value)} /></Field>
              <div className="field full">
                <label className="check"><input type="checkbox" checked={!!f.values.full_tank} onChange={(e) => f.set("full_tank", e.target.checked)} /> Залил полный бак</label>
                <span className="hint">Снимите отметку, если заправились не до пробки: тогда расход на 100 км не считается по этой заправке.</span>
              </div>
            </>
          )}
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} maxLength={isTrip ? 500 : 300} onChange={(e) => f.set("note", e.target.value)} /></Field>
        </div>
        <div className="job-stage">
          <strong>{isTrip ? "Фото накладной" : "Фото чека"}</strong>
          {files.length > 0 && (
            <div className="scan-grid job-thumbs">
              {files.map((x, i) => (
                <div className="scan" key={i}>
                  <img src={urls[i]} alt={x.name} />
                  <button className="scan-x" onClick={() => setFiles((old) => old.filter((_, j) => j !== i))} aria-label="Убрать фото" title="Убрать фото"><X size={14} aria-hidden /></button>
                </div>
              ))}
            </div>
          )}
          {f.errors.files && <span className="err" role="alert" style={{ color: "var(--error)", fontSize: 12 }}>{f.errors.files}</span>}
          <input ref={input} type="file" accept="image/*" multiple hidden onChange={(e) => { const chosen = Array.from(e.target.files ?? []); e.target.value = ""; pick(chosen); }} />
          {files.length < data.max_photos && <Button icon={Camera} onClick={() => input.current?.click()}>{files.length ? "Ещё фото" : "Сфотографировать или выбрать"}</Button>}
          <span className="muted small">Не больше {data.max_photos} фото. Можно добавить позже, пока запись свежая.</span>
        </div>
      </div>
    </Modal>
  );
}

/* ---------------- «Мои записи» (водитель) ---------------- */

export function DriverEntries() {
  const q = useQuery({ queryKey: ["entries-mine"], queryFn: () => get<Mine>("/entries/mine"), refetchInterval: 60_000 });
  const refresh = useRefresh();
  const [form, setForm] = useState<"trip" | "fuel" | null>(null);
  const d = q.data;
  const groups = useMemo(() => {
    const out: { date: string; items: Entry[] }[] = [];
    for (const it of d?.items ?? []) {
      const last = out[out.length - 1];
      if (last && last.date === it.date) last.items.push(it); else out.push({ date: it.date, items: [it] });
    }
    return out;
  }, [d]);
  const heading = (date: string) => (date === d?.today ? "Сегодня" : date === shift(d?.today ?? todayIso(), -1) ? "Вчера" : fmtDateLong(date));
  return (
    <>
      <PageHeader title="Мои записи" sub="Внесите рейс или заправку. Накладную и чек можно сфотографировать сразу, диспетчер увидит запись у себя" />
      <QueryState q={q}>
        {d && (d.vehicles.length === 0 ? (
          <Card><Empty icon={Truck} title="За вами не закреплена техника">Попросите диспетчера закрепить за вами машину, тогда здесь можно будет вносить рейсы и заправки.</Empty></Card>
        ) : (
          <div className="stack">
            <div className="entry-quick">
              <Button variant="primary" size="lg" icon={RouteIcon} disabled={!d.can.trip} onClick={() => setForm("trip")}>Рейс</Button>
              <Button variant="primary" size="lg" icon={Fuel} disabled={!d.can.fuel} onClick={() => setForm("fuel")}>Заправка</Button>
            </div>
            <OutboxList />
            {groups.length === 0 ? (
              <Card><Empty icon={RouteIcon} title="Записей пока нет">Нажмите «Рейс» после поездки или «Заправка» после заправки. Здесь будут записи за последние два месяца.</Empty></Card>
            ) : groups.map((g) => (
              <section key={g.date} className="stack-sm">
                <h2 className="row" style={{ gap: 8 }}>{heading(g.date)} <Badge tone="neutral">{g.items.length} {plural(g.items.length, "запись", "записи", "записей")}</Badge></h2>
                <div className="job-list">{g.items.map((it) => <EntryCard key={`${it.kind}-${it.id}`} e={it} showWho={!it.mine} onChanged={refresh} />)}</div>
              </section>
            ))}
          </div>
        ))}
      </QueryState>
      {d && form && <EntryModal kind={form} data={d} onClose={() => setForm(null)} onDone={refresh} />}
    </>
  );
}

/* ---------------- «Записи водителей» (лента сотрудника) ---------------- */

export function EntriesFeed() {
  const entity = useEntityParam();
  const [sp, setSp] = useSearchParams();
  const kind = sp.get("kind") || "";
  const to = sp.get("to") || todayIso();
  const from = sp.get("from") || shift(to, -6);
  const problems = sp.get("problems") === "1";
  const set = (patch: Record<string, string>) => setSp((old) => { const n = new URLSearchParams(old); for (const [k, v] of Object.entries(patch)) v ? n.set(k, v) : n.delete(k); return n; }, { replace: true });
  const q = useQuery({
    queryKey: ["entries-feed", entity, kind, from, to, problems],
    queryFn: () => get<Feed>("/entries/feed", { legal_entity_id: entity, kind, date_from: from, date_to: to, problems }),
    placeholderData: (p) => p, refetchInterval: 60_000,
  });
  const refresh = useRefresh();
  const d = q.data;
  return (
    <>
      <PageHeader title="Записи водителей" sub="Рейсы и заправки, которые внесли водители: с фото накладных и чеков. Подозрительные заправки отмечены красным"
        actions={<><Link className="btn" to="/trips">Журнал рейсов</Link><Link className="btn" to="/fuel-fills">Журнал заправок</Link></>} />
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <input className="input compact" type="date" aria-label="С даты" value={from} max={to} onChange={(e) => e.target.value && set({ from: e.target.value })} />
        <span className="muted">—</span>
        <input className="input compact" type="date" aria-label="По дату" value={to} onChange={(e) => e.target.value && set({ to: e.target.value, from: "" })} />
        <select className="input compact" aria-label="Что показать" value={kind} onChange={(e) => set({ kind: e.target.value })}>
          <option value="">Рейсы и заправки</option><option value="trip">Только рейсы</option><option value="fuel">Только заправки</option>
        </select>
        <label className="check"><input type="checkbox" checked={problems} onChange={(e) => set({ problems: e.target.checked ? "1" : "" })} /> Только подозрительные</label>
      </div>
      <QueryState q={q}>
        {d && (
          <div className="stack">
            <div className="row wrap" style={{ gap: 8 }}>
              <Badge tone="neutral">Рейсов: {d.totals.trips}</Badge>
              <Badge tone="neutral">Заправок: {d.totals.fills}</Badge>
              {d.totals.fills > 0 && <Badge tone="info">Залито: {fmtNum(d.totals.liters, 1)} л на {fmtMoney(d.totals.amount)} ₽</Badge>}
            </div>
            {d.truncated && <Alert tone="info">Показаны самые свежие записи. Сузьте даты или откройте журнал, чтобы увидеть остальные.</Alert>}
            {d.items.length === 0 ? (
              <Card><Empty icon={RouteIcon} title={problems ? "Подозрительных записей нет" : "За эти дни записей нет"}>{problems ? "Все заправки в пределах бака и нормы расхода." : "Измените даты или дождитесь, когда водители внесут рейсы и заправки."}</Empty></Card>
            ) : (
              <div className="job-list">{d.items.map((it) => <EntryCard key={`${it.kind}-${it.id}`} e={it} showWho onChanged={refresh} />)}</div>
            )}
          </div>
        )}
      </QueryState>
    </>
  );
}

/* ---------------- фото из журналов рейсов и заправок ---------------- */

function EntryPhotosModal({ kind, id, onClose }: { kind: "trip" | "fuel"; id: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ["entry-one", kind, id], queryFn: () => get<{ item: Entry }>(`/entries/${kind}/${id}`) });
  const refresh = useRefresh();
  return (
    <Modal title={kind === "trip" ? "Рейс: накладная" : "Заправка: чек"} onClose={onClose} footer={<Button variant="primary" onClick={onClose}>Закрыть</Button>}>
      <QueryState q={q}>
        {q.data && (q.data.item.pics.length === 0 && !q.data.item.can.photo
          ? <p className="muted">Фото к этой записи нет.</p>
          : <EntryPhotos e={q.data.item} onChanged={refresh} />)}
      </QueryState>
    </Modal>
  );
}

export function EntryPhotosButton({ kind, id, count }: { kind: "trip" | "fuel"; id: number; count?: number | string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="ghost" size="sm" icon={Camera} aria-label={count ? `Фото: ${count}` : "Фото"} title={count ? `Фото: ${count}` : "Фото"} onClick={() => setOpen(true)} />
      {open && <EntryPhotosModal kind={kind} id={id} onClose={() => setOpen(false)} />}
    </>
  );
}
