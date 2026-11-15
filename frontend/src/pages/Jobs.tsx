// Наряды на день: доска дня для диспетчера («/jobs») и «Мои наряды» для водителя («/jobs/mine»).
// Создание и правка наряда идут через общую форму разделов (Registry), здесь отметки, состояния и фото.
import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, CalendarCheck, CalendarPlus, Camera, ChevronLeft, ChevronRight, Copy, ExternalLink, MapPin, Pencil, Plus, Truck, X } from "lucide-react";
import { get, post, upload, errorText } from "../api";
import { shrinkAll } from "../images";
import { enqueue, isOffline, outboxAvailable } from "../outbox";
import { OutboxList, useJobPending } from "../components/Outbox";
import { fmtDateLong, fmtDateTime, fmtSize, plural, todayIso } from "../format";
import { useAuth } from "../auth";
import { useEntityParam } from "../hooks";
import { twogisSearch } from "../maps";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, Modal, PageHeader, QueryState, Tone, useToast } from "../ui";
import { RecordModal, type Meta, type Row } from "./Registry";

interface Photo { id: number; stage: "before" | "after"; name: string; size: number; has_thumb: boolean; added_at: string; added_by: string }
export interface Job {
  id: number; job_date: string; time_from: string; vehicle_id: number; vehicle_label: string; plate: string; customer: string; address: string; task: string;
  state: string; state_label: string; status: { text: string; tone: Tone }; late: boolean; legal_entity_id: number;
  arrived_at: string | null; arrived_by: string; finished_at: string | null; finished_by: string; result_note: string; archived: boolean; archive_reason: string;
  photos: { before: Photo[]; after: Photo[] };
  can: { arrive: boolean; finish: boolean; photo_before: boolean; photo_after: boolean; remove_photo: boolean; edit: boolean; state: boolean; copy: boolean; void: boolean };
}
interface Day {
  date: string; today: string; items: Job[]; stale: Job[]; stale_total: number; can_create: boolean;
  counts: { total: number; planned: number; on_site: number; done: number; cancelled: number };
}
export interface Mine { today: string; items: Job[]; has_vehicle: boolean }

const STATES = [{ v: "planned", l: "Запланирован" }, { v: "on_site", l: "На объекте" }, { v: "done", l: "Выполнен" }, { v: "cancelled", l: "Отменён" }];
const photoUrl = (id: number, thumb = false) => `/api/jobs/photos/${id}${thumb ? "?thumb=true" : ""}`;
const hhmm = (iso: string | null) => (iso ? fmtDateTime(iso) : "");
const weekday = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString("ru-RU", { weekday: "long" });
const shift = (iso: string, days: number) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + days); return d.toISOString().slice(0, 10); };

/** Всё, что меняет наряд, обновляет и доску, и «Мои наряды», и счётчики на главной. */
export function useRefresh() {
  const qc = useQueryClient();
  return () => { for (const k of ["jobs-day", "jobs-mine", "reg", "dashboard", "driver-home"]) qc.invalidateQueries({ queryKey: [k] }); };
}

/* ---------------- фото ---------------- */

function PhotoStage({ job, stage, title, canAdd, onChanged }: { job: Job; stage: "before" | "after"; title: string; canAdd: boolean; onChanged: () => void }) {
  const items = job.photos[stage];
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [view, setView] = useState<Photo | null>(null);
  const [removing, setRemoving] = useState<Photo | null>(null);
  const pending = useJobPending(job.id).photos(stage);
  const add = useMutation({
    mutationFn: async (files: File[]) => {
      const small = await shrinkAll(files);
      const f = new FormData();
      f.set("stage", stage);
      small.forEach((x) => f.append("files", x));
      try {
        return await upload<{ queued?: undefined }>(`/jobs/${job.id}/photos`, f);
      } catch (e) {
        if (!isOffline(e) || !outboxAvailable()) throw e;
        await enqueue({ kind: "job_photos", title: `Фото ${stage === "before" ? "до" : "после"} работы · ${job.plate} · ${job.address}`, url: `/jobs/${job.id}/photos`,
          fields: { stage }, files: small.map((x) => ({ name: x.name, blob: x })), job_id: job.id });
        return { queued: true as const };
      }
    },
    onSuccess: (r) => { if (r.queued) toast.success("Нет связи: фото сохранены в телефоне и уйдут сами"); else onChanged(); },     // фото появляется в карточке само
    onError: (e) => toast.error(errorText(e)),
  });
  return (
    <div className="job-stage">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{title}</strong>
        <span className="muted small">{items.length ? `${items.length} из 10` : "нет фото"}</span>
      </div>
      {pending > 0 && <Badge tone="warning">В телефоне ждут отправки: {pending}</Badge>}
      {items.length > 0 && (
        <div className="scan-grid job-thumbs">
          {items.map((ph) => (
            <div className="scan" key={ph.id}>
              <button className="scan-open" onClick={() => setView(ph)} aria-label={`Открыть фото: ${ph.name}`}>
                <img src={photoUrl(ph.id, true)} alt="" loading="lazy" />
              </button>
              {job.can.remove_photo && <button className="scan-x" onClick={() => setRemoving(ph)} aria-label="Убрать фото" title="Убрать фото"><X size={14} aria-hidden /></button>}
            </div>
          ))}
        </div>
      )}
      {canAdd && (
        <>
          <input ref={input} type="file" accept="image/*" multiple hidden onChange={(e) => { const files = Array.from(e.target.files ?? []); e.target.value = ""; if (files.length) add.mutate(files); }} />
          <Button icon={Camera} loading={add.isPending} onClick={() => input.current?.click()}>Добавить фото</Button>
        </>
      )}
      {view && (
        <Modal title={`${title}: ${view.name}`} wide onClose={() => setView(null)}
          footer={<><a className="btn" href={photoUrl(view.id)} target="_blank" rel="noreferrer"><ExternalLink size={16} aria-hidden /> Открыть отдельно</a><Button variant="primary" onClick={() => setView(null)}>Закрыть</Button></>}>
          <div className="viewer"><img src={photoUrl(view.id)} alt={view.name} /></div>
          <div className="muted small" style={{ marginTop: 8 }}>{fmtDateTime(view.added_at)}{view.added_by ? ` · ${view.added_by}` : ""} · {fmtSize(view.size)}</div>
        </Modal>
      )}
      {removing && (
        <ConfirmModal title="Убрать фото" danger confirmLabel="Убрать" reasonLabel="Причина" reasonRequired={false}
          text="Фото пропадёт из наряда, но останется в журнале. Причину можно не писать."
          onConfirm={async (reason) => { await post(`/jobs/photos/${removing.id}/remove`, { reason }); onChanged(); }} onClose={() => setRemoving(null)} />
      )}
    </div>
  );
}

/* ---------------- карточка наряда ---------------- */

function FinishModal({ job, onClose, onDone }: { job: Job; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState("");
  const toast = useToast();
  const finish = useMutation({
    mutationFn: async () => {
      const at = new Date().toISOString();            // время нажатия: если связи нет, отметка уйдёт позже, но время останется верным
      try {
        return await post<{ queued?: undefined }>(`/jobs/${job.id}/finish`, { note });
      } catch (e) {
        if (!isOffline(e) || !outboxAvailable()) throw e;
        await enqueue({ kind: "job_finish", title: `Наряд выполнен · ${job.plate} · ${job.address}`, url: `/jobs/${job.id}/finish`, json: { note, at }, job_id: job.id });
        return { queued: true as const };
      }
    },
    onSuccess: (r) => { onDone(); toast.success(r.queued ? "Нет связи: отметка сохранена в телефоне и уйдёт сама" : "Наряд выполнен. Спасибо!"); onClose(); },
    onError: (e) => toast.error(errorText(e)),
  });
  const noAfter = job.photos.after.length === 0;
  return (
    <Modal title="Закончить наряд" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={finish.isPending} onClick={() => finish.mutate()}>Наряд выполнен</Button></>}>
      <div className="stack-sm">
        <div><strong>{job.plate}</strong> · {job.address}</div>
        {noAfter && <Alert tone="warning">Фото «после работы» ещё нет. Лучше сначала сфотографировать результат: после выполнения это можно сделать, но проще сейчас.</Alert>}
        <Field label="Что сделано (необязательно)" hint="Например: вывезли 3 рейса, остался один сугроб у ворот">
          <textarea className="input" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

export function JobCard({ job, driver, onEdit, onChanged }: { job: Job; driver: boolean; onEdit?: (j: Job) => void; onChanged: () => void }) {
  const toast = useToast();
  const [finishing, setFinishing] = useState(false);
  const [voiding, setVoiding] = useState(false);
  const pend = useJobPending(job.id);
  const run = useMutation({
    mutationFn: async ({ url, body }: { url: string; body?: unknown }) => {
      try {
        return await post<{ changed?: boolean; job: Job; queued?: undefined }>(url, body ?? {});
      } catch (e) {
        if (!url.endsWith("/arrive") || !isOffline(e) || !outboxAvailable()) throw e;
        // без связи: «приехал» запоминается вместе со временем нажатия и уходит, когда сеть появится
        await enqueue({ kind: "job_arrive", title: `Приехал · ${job.plate} · ${job.address}`, url, json: { at: new Date().toISOString() }, job_id: job.id });
        return { queued: true as const };
      }
    },
    onSuccess: (r, v) => {
      onChanged();
      if (r.queued) toast.success("Нет связи: отметка «приехал» сохранена в телефоне и уйдёт сама. Фото «до работы» можно сделать сейчас");
      else if (v.url.endsWith("/arrive")) toast.success(r.changed === false ? "Отметка уже стоит" : "Отмечено: вы на объекте. Сделайте фото «до работы»");
      else if (v.url.endsWith("/copy")) toast.success(`Наряд повторён на ${fmtDateLong(r.job.job_date)}`);
    },
    onError: (e) => toast.error(errorText(e)),
  });
  const busy = run.isPending;
  const big = driver ? "lg" : "md";
  return (
    <div className={`card job-card ${job.late ? "late" : ""} ${job.state === "cancelled" ? "cancelled" : ""}`}>
      <div className="job-head">
        <span className="job-time">{job.time_from || "—"}</span>
        <div className="job-title">
          <strong><Truck size={14} aria-hidden /> {job.plate}</strong>
          <span className="muted small">{job.vehicle_label.replace(job.plate + " · ", "")}</span>
        </div>
        <Badge tone={job.status.tone}>{job.status.text}</Badge>
      </div>
      <div className="job-body">
        {job.customer && <div className="job-customer">{job.customer}</div>}
        <div className="row" style={{ alignItems: "flex-start", gap: 6 }}>
          <MapPin size={15} aria-hidden style={{ flex: "none", marginTop: 3, color: "var(--mint)" }} />
          <span>{job.address} <a href={twogisSearch(job.address)} target="_blank" rel="noreferrer" className="small">на карте</a></span>
        </div>
        {job.task && <div className="job-task">{job.task}</div>}
        {(job.arrived_at || job.finished_at) && (
          <div className="muted small">
            {job.arrived_at && <span>Приехал: {hhmm(job.arrived_at)}{job.arrived_by ? ` · ${job.arrived_by}` : ""}</span>}
            {job.arrived_at && job.finished_at && " · "}
            {job.finished_at && <span>Закончил: {hhmm(job.finished_at)}{job.finished_by ? ` · ${job.finished_by}` : ""}</span>}
          </div>
        )}
        {job.result_note && <div className="job-note"><span className="muted small">Итог:</span> {job.result_note}</div>}
        {job.archived && <Alert tone="warning">Наряд в архиве{job.archive_reason ? `: ${job.archive_reason}` : ""}</Alert>}
        {pend.arrive && <Alert tone="warning">Отметка «Приехал» сохранена в телефоне и ждёт связи</Alert>}
        {pend.finish && <Alert tone="warning">Отметка «Закончил» сохранена в телефоне и ждёт связи</Alert>}
        {pend.failed.map((x) => <Alert key={x.id} tone="error">{x.title}: {x.error}</Alert>)}
      </div>

      <div className="job-photos">
        <PhotoStage job={job} stage="before" title="До работы" canAdd={job.can.photo_before} onChanged={onChanged} />
        <PhotoStage job={job} stage="after" title="После работы" canAdd={job.can.photo_after} onChanged={onChanged} />
      </div>

      <div className="job-actions">
        {job.can.arrive && !pend.arrive && !pend.finish && <Button variant="primary" size={big} icon={MapPin} loading={busy} onClick={() => run.mutate({ url: `/jobs/${job.id}/arrive` })}>Приехал</Button>}
        {job.can.finish && !pend.finish && <Button variant={job.can.arrive && !pend.arrive ? "default" : "primary"} size={big} icon={CalendarCheck} disabled={busy} onClick={() => setFinishing(true)}>Закончил</Button>}
        {!driver && (
          <>
            <span className="spacer" />
            {job.can.state && (
              <select className="input compact" aria-label="Состояние наряда" value={job.state} disabled={busy}
                onChange={(e) => run.mutate({ url: `/jobs/${job.id}/state`, body: { state: e.target.value } })}>
                {STATES.map((s) => <option key={s.v} value={s.v}>{s.l}</option>)}
              </select>
            )}
            {job.can.copy && <Button variant="ghost" size="sm" icon={Copy} title="Повторить на следующий день" aria-label="Повторить на следующий день" disabled={busy} onClick={() => run.mutate({ url: `/jobs/${job.id}/copy` })} />}
            {job.can.edit && onEdit && <Button variant="ghost" size="sm" icon={Pencil} title="Изменить" aria-label="Изменить" onClick={() => onEdit(job)} />}
            {job.can.void && !job.archived && <Button variant="ghost" size="sm" icon={Archive} title="В архив" aria-label="В архив" onClick={() => setVoiding(true)} />}
          </>
        )}
      </div>
      {finishing && <FinishModal job={job} onClose={() => setFinishing(false)} onDone={onChanged} />}
      {voiding && (
        <ConfirmModal title="Убрать наряд в архив" danger confirmLabel="В архив" reasonLabel="Причина"
          text="Наряд не удаляется: его можно найти в журнале нарядов и вернуть. Водитель его больше не увидит."
          onConfirm={async (reason) => { await post(`/jobs/${job.id}/void`, { reason }); onChanged(); toast.success("Наряд в архиве"); }} onClose={() => setVoiding(false)} />
      )}
    </div>
  );
}

/* ---------------- доска дня ---------------- */

export function JobsBoard() {
  const entity = useEntityParam();
  const { can } = useAuth();
  const [sp, setSp] = useSearchParams();
  const date = sp.get("date") || todayIso();
  const setDate = (d: string) => setSp((old) => { const n = new URLSearchParams(old); d && d !== todayIso() ? n.set("date", d) : n.delete("date"); return n; }, { replace: true });
  const q = useQuery({ queryKey: ["jobs-day", date, entity], queryFn: () => get<Day>("/jobs/day", { date, legal_entity_id: entity }), placeholderData: (p) => p, refetchInterval: 60_000 });
  const meta = useQuery({ queryKey: ["reg-meta", "jobs"], queryFn: () => get<Meta>("/jobs/meta"), staleTime: 60_000 });
  const refresh = useRefresh();
  const toast = useToast();
  const [edit, setEdit] = useState<Row | "new" | null>(null);
  const [showStale, setShowStale] = useState(false);
  const d = q.data;

  const asRow = (j: Job): Row => ({ ...j, id: j.id, vehicle_id_label: j.vehicle_label, can_edit: j.can.edit, can_void: j.can.void, archived: j.archived, archive_reason: j.archive_reason, legal_entity_id: j.legal_entity_id });
  const open = d ? d.counts.planned + d.counts.on_site : 0;

  return (
    <>
      <PageHeader title="Наряды на день" sub="Куда едет каждая машина: адрес, задача, отметки «приехал» и «закончил», фото до и после"
        actions={<>
          {can("map.view") && <Link className="btn" to={date === todayIso() ? "/map" : `/map?date=${date}`}><MapPin size={16} aria-hidden /> На карте</Link>}
          <Link className="btn" to="/jobs/list">Журнал</Link>
          {d?.can_create && <Button variant="primary" icon={Plus} onClick={() => setEdit("new")}>Новый наряд</Button>}
        </>} />
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <Button icon={ChevronLeft} aria-label="Предыдущий день" onClick={() => setDate(shift(date, -1))} />
        <input className="input compact" type="date" aria-label="Дата" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
        <Button icon={ChevronRight} aria-label="Следующий день" onClick={() => setDate(shift(date, 1))} />
        {date !== todayIso() && <Button onClick={() => setDate("")}>Сегодня</Button>}
        <strong style={{ textTransform: "capitalize" }}>{weekday(date)}</strong>
        <span className="muted">{fmtDateLong(date)}</span>
      </div>
      <QueryState q={q}>
        {d && (
          <div className="stack">
            {d.stale_total > 0 && (
              <Alert tone="warning">
                Не закрыто нарядов за прошлые дни: {d.stale_total}. Их нужно закрыть или отменить.{" "}
                <button className="linklike" onClick={() => setShowStale((v) => !v)}>{showStale ? "Скрыть" : "Показать"}</button>
              </Alert>
            )}
            {showStale && <div className="job-list">{d.stale.map((j) => <JobCard key={j.id} job={j} driver={false} onEdit={(x) => setEdit(asRow(x))} onChanged={refresh} />)}</div>}
            <div className="row wrap" style={{ gap: 8 }}>
              <Badge tone="neutral">Всего: {d.counts.total}</Badge>
              <Badge tone={open ? "warning" : "neutral"}>Не закончено: {open}</Badge>
              <Badge tone="success">Выполнено: {d.counts.done}</Badge>
              {d.counts.cancelled > 0 && <Badge tone="neutral">Отменено: {d.counts.cancelled}</Badge>}
            </div>
            {d.items.length === 0 ? (
              <Card><Empty icon={CalendarPlus} title="На этот день нарядов нет">{d.can_create ? "Нажмите «Новый наряд» или повторите вчерашний кнопкой «Повторить на следующий день»." : undefined}</Empty></Card>
            ) : (
              <div className="job-list">{d.items.map((j) => <JobCard key={j.id} job={j} driver={false} onEdit={(x) => setEdit(asRow(x))} onChanged={refresh} />)}</div>
            )}
          </div>
        )}
      </QueryState>
      {meta.data && edit && (
        <RecordModal meta={meta.data} row={edit === "new" ? null : edit} defaults={edit === "new" ? { job_date: date } : undefined} onClose={() => setEdit(null)}
          onSaved={() => { refresh(); toast.success("Сохранено"); }} />
      )}
    </>
  );
}

/* ---------------- «Мои наряды» (водитель) ---------------- */

export function MyJobs() {
  const q = useQuery({ queryKey: ["jobs-mine"], queryFn: () => get<Mine>("/jobs/mine"), refetchInterval: 60_000 });
  const refresh = useRefresh();
  const d = q.data;
  const groups = d ? [
    { title: "Не закрыты за прошлые дни", tone: "warning" as Tone, items: d.items.filter((j) => j.job_date < d.today) },
    { title: "Сегодня", tone: "mint" as Tone, items: d.items.filter((j) => j.job_date === d.today) },
    { title: "Завтра", tone: "info" as Tone, items: d.items.filter((j) => j.job_date > d.today) },
  ].filter((g) => g.items.length) : [];
  return (
    <>
      <PageHeader title="Мои наряды" sub="Нажмите «Приехал», когда будете на объекте, сфотографируйте до работы и после, затем «Закончил»" />
      <QueryState q={q}>
        {d && (!d.has_vehicle ? (
          <Card><Empty icon={Truck} title="За вами не закреплена техника">Попросите диспетчера закрепить за вами машину, тогда здесь появятся наряды.</Empty></Card>
        ) : groups.length === 0 ? (
          <Card><Empty icon={CalendarCheck} title="Нарядов на сегодня и завтра нет">Когда диспетчер добавит наряд, он появится здесь.</Empty></Card>
        ) : (
          <div className="stack">
            <OutboxList />
            {groups.map((g) => (
              <section key={g.title} className="stack-sm">
                <h2 className="row" style={{ gap: 8 }}>{g.title} <Badge tone={g.tone}>{g.items.length} {plural(g.items.length, "наряд", "наряда", "нарядов")}</Badge></h2>
                <div className="job-list">{g.items.map((j) => <JobCard key={j.id} job={j} driver onChanged={refresh} />)}</div>
              </section>
            ))}
          </div>
        ))}
      </QueryState>
    </>
  );
}
