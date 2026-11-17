// Тоннаж: талоны весов с полигона. Водитель прикладывает только фото и заметку, тонны программа читает с фото сама.
// Здесь вкладка водителя («/tonnage»: талоны за месяц, итог) и то, что нужно главной странице: окно внесения талона и блок «Тоннаж за месяц».
// Журнал для диспетчера и директора — общий раздел «Журнал тоннажа» (Registry), отчёт по машинам — «Отчёты → Тоннаж за месяц».
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, ChevronLeft, ChevronRight, Trash2, Truck, Weight, X } from "lucide-react";
import { ApiError, get, post, upload, errorText } from "../api";
import { shrinkAll } from "../images";
import { enqueue, isOffline, outboxAvailable } from "../outbox";
import { OutboxList } from "../components/Outbox";
import { fmtDate, fmtNum, plural } from "../format";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, Modal, PageHeader, QueryState, Spinner, useForm, useToast } from "../ui";
import { EntryPhotos, newId, plateOf, type Entry } from "./Entries";
import { currentMonth, shiftMonth } from "./Timesheet";

export interface TonnageData {
  vehicles: { id: number; label: string; legal_entity_id: number }[];
  items: Entry[]; today: string; max_photos: number; delete_hours: number;
  month: string; month_label: string; prev_month: string; is_current: boolean;
  totals: { trips: number; tons: number; pending: number };
  can: { create: boolean };
}

const KEYS = ["tonnage", "driver-home", "dashboard", "reg", "entry-one"];
const useRefresh = () => {
  const qc = useQueryClient();
  return () => { for (const k of KEYS) qc.invalidateQueries({ queryKey: [k] }); };
};

export const fmtTons = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${fmtNum(v, 3)} т`);
const trips = (n: number) => `${n} ${plural(n, "ходка", "ходки", "ходок")}`;
/** Программа ещё читает фото: тонн нет, а чтение не закончено (с фото). Тогда страница сама обновляется чаще. */
const isReading = (e: Entry) => e.tons == null && e.tons_state?.text === "Читаю фото…";

export function useTonnage(month?: string, enabled = true) {
  const q = useQuery({
    queryKey: ["tonnage", month ?? "now"],
    queryFn: () => get<TonnageData>("/tonnage/mine", month ? { month } : undefined),
    enabled,
    refetchInterval: (query) => ((query.state.data as TonnageData | undefined)?.items.some(isReading) ? 4_000 : 60_000),
  });
  return q;
}

/* ---------------- окно «Талон весов» ---------------- */

export function TicketModal({ data, onClose, onDone }: { data: TonnageData; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const clientId = useRef(newId()).current;            // повторная отправка (связь пропала) не создаст второй талон
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [queued, setQueued] = useState(false);
  const [general, setGeneral] = useState("");
  const f = useForm({ vehicle_id: String(data.vehicles[0]?.id ?? ""), note: "" });
  const urls = useMemo(() => files.map((x) => URL.createObjectURL(x)), [files]);
  useEffect(() => () => urls.forEach((u) => URL.revokeObjectURL(u)), [urls]);
  const vehicle = data.vehicles.find((v) => String(v.id) === f.values.vehicle_id);

  const send = useMutation({
    mutationFn: async () => {
      if (!files.length) throw new ApiError(422, "no_file", "Приложите фото талона", "files");
      const body = { vehicle_id: Number(f.values.vehicle_id), ticket_date: data.today, note: f.values.note, client_id: clientId };
      const form = new FormData();
      form.set("data", JSON.stringify(body));
      files.forEach((x) => form.append("files", x));
      try {
        await upload("/entries/mine/ticket", form);
        return { queued: false };
      } catch (e) {
        if (!isOffline(e) || !outboxAvailable()) throw e;
        await enqueue({ kind: "ticket", title: `Талон весов · ${plateOf(vehicle?.label ?? "")}`, url: "/entries/mine/ticket", fields: { data: JSON.stringify(body) }, files: files.map((x) => ({ name: x.name, blob: x })) });
        return { queued: true };
      }
    },
    onSuccess: (r) => {
      onDone();
      if (r.queued) setQueued(true);
      else { toast.success("Талон принят. Тонны программа прочитает с фото сама"); onClose(); }
    },
    onError: (e) => { f.fail(e); setGeneral(e instanceof ApiError && (e.field || e.fields) ? "" : errorText(e)); },
  });

  const pick = async (chosen: File[]) => {
    const room = Math.max(data.max_photos - files.length, 0);
    const small = await shrinkAll(chosen.slice(0, room));
    setFiles((old) => [...old, ...small].slice(0, data.max_photos));
    f.clear();
  };

  if (queued) {
    return (
      <Modal title="Сохранено в телефоне" onClose={onClose} footer={<Button variant="primary" onClick={onClose}>Понятно</Button>}>
        <div className="stack-sm">
          <Alert tone="warning">Связи нет, поэтому талон пока только в вашем телефоне.</Alert>
          <p>Как только сеть появится, он уйдёт сам. Ничего повторно вносить не нужно. Список неотправленного виден наверху этой страницы.</p>
          <p className="muted small">Не закрывайте и не удаляйте программу на телефоне, пока талон не уйдёт.</p>
        </div>
      </Modal>
    );
  }
  return (
    <Modal title="Талон весов" onClose={onClose}
      footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={send.isPending} onClick={() => { f.clear(); setGeneral(""); send.mutate(); }}>Отправить талон</Button></>}>
      <div className="stack">
        {general && <Alert tone="error">{general}</Alert>}
        <div className="job-stage">
          <strong>Фото талона</strong>
          <span className="muted small">Сфотографируйте талон с полигона целиком, чтобы были видны брутто, тара и нетто. Тонны программа прочитает сама.</span>
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
          <input ref={input} type="file" accept="image/*" multiple hidden onChange={(e) => { const chosen = Array.from(e.target.files ?? []); e.target.value = ""; void pick(chosen); }} />
          {files.length < data.max_photos && <div className="row" style={{ alignSelf: "stretch" }}><Button icon={Camera} size="lg" onClick={() => input.current?.click()}>{files.length ? "Ещё фото" : "Сфотографировать талон"}</Button></div>}
        </div>
        {data.vehicles.length > 1 && (
          <Field label="Машина" error={f.errors.vehicle_id}>
            <select className="input" value={f.values.vehicle_id} onChange={(e) => f.set("vehicle_id", e.target.value)}>
              {data.vehicles.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
            </select>
          </Field>
        )}
        {data.vehicles.length === 1 && <div className="muted small"><Truck size={14} aria-hidden /> {data.vehicles[0].label}</div>}
        <Field label="Заметка" error={f.errors.note} hint="Можно не писать. Например: куда возили, что было с весами">
          <textarea className="input" value={f.values.note} maxLength={500} onChange={(e) => f.set("note", e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

/* ---------------- один талон в списке ---------------- */

function stateBadge(e: Entry) {
  if (e.tons != null) return <Badge tone="success">{fmtTons(e.tons)}</Badge>;
  if (isReading(e)) return <Badge tone="neutral">Читаю фото…</Badge>;
  return <Badge tone="warning">Вес впишет диспетчер</Badge>;
}

export function TicketCard({ e, onChanged }: { e: Entry; onChanged: () => void }) {
  const toast = useToast();
  const [deleting, setDeleting] = useState(false);
  return (
    <div className="card job-card entry-card">
      <div className="job-head">
        <span className="entry-ico" aria-hidden><Weight size={18} /></span>
        <div className="job-title">
          <strong><Truck size={14} aria-hidden /> {plateOf(e.vehicle_id_label)}</strong>
          <span className="muted small">{fmtDate(e.date)}</span>
        </div>
        {stateBadge(e)}
      </div>
      {e.note && <div className="job-body"><div className="job-note">{e.note}</div></div>}
      {(e.pics.length > 0 || e.can.photo) && <div className="job-photos single"><EntryPhotos e={e} onChanged={onChanged} /></div>}
      {e.can.delete && (
        <div className="job-actions">
          <span className="spacer" />
          <Button variant="ghost" size="sm" icon={Trash2} onClick={() => setDeleting(true)}>Убрать талон</Button>
        </div>
      )}
      {deleting && (
        <ConfirmModal title="Убрать талон" danger confirmLabel="Убрать"
          text="Талон пропадёт из списка и из итога. Убрать свой талон можно только в первые сутки."
          onConfirm={async () => { await post(`/entries/mine/ticket/${e.id}/delete`); onChanged(); toast.success("Талон убран"); }}
          onClose={() => setDeleting(false)} />
      )}
    </div>
  );
}

/* ---------------- вкладка «Тоннаж» ---------------- */

export function Tonnage() {
  const [month, setMonth] = useState(currentMonth());
  const q = useTonnage(month);
  const refresh = useRefresh();
  const [form, setForm] = useState(false);
  const d = q.data;
  return (
    <>
      <PageHeader title="Тоннаж" sub="Талоны весов с полигона: сфотографируйте талон после каждой ходки"
        actions={d?.can.create ? <Button variant="primary" icon={Camera} onClick={() => setForm(true)}>Талон весов</Button> : undefined} />
      <div className="stack">
        <OutboxList />
        <div className="row" style={{ gap: 8 }}>
          <Button icon={ChevronLeft} aria-label="Прошлый месяц" onClick={() => setMonth(shiftMonth(month, -1))} />
          <strong style={{ minWidth: 140, textAlign: "center" }}>{d ? d.month_label : month}</strong>
          <Button icon={ChevronRight} aria-label="Следующий месяц" disabled={month >= currentMonth()} onClick={() => setMonth(shiftMonth(month, 1))} />
        </div>
        <QueryState q={q}>
          {d && (
            <div className="stack">
              {!d.can.create && d.vehicles.length === 0 && (
                <Card><Empty icon={Weight} title="Нет машины, по которой ведётся тоннаж">Попросите диспетчера закрепить за вами мусоровоз или самосвал. Если машина уже закреплена, в её карточке должна стоять галочка «Вести тоннаж».</Empty></Card>
              )}
              {(d.vehicles.length > 0 || d.items.length > 0) && (
                <div className="ton-kpis" aria-label="Итог за месяц">
                  <Card><span className="muted small">Привезено за месяц</span><span className="pay-big">{fmtNum(d.totals.tons, 2)} т</span></Card>
                  <Card><span className="muted small">Ходок</span><span className="pay-big">{d.totals.trips}</span></Card>
                  {d.totals.pending > 0 && <Card><span className="muted small">Ждут веса</span><span className="pay-big">{d.totals.pending}</span><span className="muted small">диспетчер впишет по фото</span></Card>}
                </div>
              )}
              {d.items.length === 0 && d.vehicles.length > 0 ? (
                <Card><Empty icon={Weight} title={d.is_current ? "В этом месяце талонов пока нет" : "В этом месяце талонов не было"}>{d.is_current ? "После ходки на полигон нажмите «Талон весов» и сфотографируйте талон." : undefined}</Empty></Card>
              ) : (
                <div className="job-list">{d.items.map((e) => <TicketCard key={e.id} e={e} onChanged={refresh} />)}</div>
              )}
            </div>
          )}
        </QueryState>
      </div>
      {d && form && <TicketModal data={d} onClose={() => setForm(false)} onDone={refresh} />}
    </>
  );
}

/* ---------------- блок на главной водителя ---------------- */

export function TonnageBlock({ onAdd }: { onAdd: () => void }) {
  const q = useTonnage();
  const refresh = useRefresh();
  const d = q.data;
  if (q.isLoading) return <Spinner />;
  if (!d) return null;
  return (
    <section className="stack-sm">
      <h2 className="row" style={{ gap: 8 }}><Weight size={18} aria-hidden /> Тоннаж за {d.month_label}
        <Link className="small" to="/tonnage">Все талоны</Link></h2>
      <div className="ton-kpis">
        <Card><span className="muted small">Привезено</span><span className="pay-big">{fmtNum(d.totals.tons, 2)} т</span></Card>
        <Card><span className="muted small">Ходок</span><span className="pay-big">{d.totals.trips}</span></Card>
        {d.totals.pending > 0 && <Card><span className="muted small">Ждут веса</span><span className="pay-big">{d.totals.pending}</span></Card>}
      </div>
      {d.items.length === 0
        ? <Card><p className="muted">Талонов в этом месяце пока нет. <button className="link-btn" onClick={onAdd}>Добавить талон</button></p></Card>
        : <div className="job-list">{d.items.slice(0, 3).map((e) => <TicketCard key={e.id} e={e} onChanged={refresh} />)}</div>}
      {d.items.length > 3 && <p className="muted small">Показаны последние 3 из {trips(d.items.length)}. <Link to="/tonnage">Открыть все</Link></p>}
    </section>
  );
}
