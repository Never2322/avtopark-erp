// «Фото ремонта»: механик или директор фотографирует технику после ремонта и записывает, что отремонтировали.
// Здесь же вся история (любой ремонт можно открыть и посмотреть в любое время) и кнопка «Подтвердить» для директора.
// Подтверждённая запись не меняется, пока директор не снимет подтверждение. Фото и записи не стираются.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Camera, CheckCircle2, ChevronLeft, ChevronRight, ExternalLink, ImagePlus, Pencil, Search, Undo2, Wrench, X } from "lucide-react";
import { ApiError, errorText, get, patch, post, upload } from "../api";
import { fmtDate, fmtDateTime, plural, todayIso } from "../format";
import { shrinkAll } from "../images";
import { useAuth } from "../auth";
import { useDebounced, useEntityParam, useUrlState } from "../hooks";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, FilterChips, Modal, PageHeader, Pagination, QueryState, useForm, useToast } from "../ui";

interface Item {
  id: number; legal_entity_id: number; vehicle_id: number; vehicle: string; plate: string; repair_date: string; what: string;
  request_id: number | null; request_number: number | null; request_title: string | null;
  photo_count: number; preview: number[]; confirmed: boolean; confirmed_at: string | null; confirmed_by: string | null;
  created_at: string; created_by: string | null; archived: boolean; archive_reason: string; can_edit: boolean; can_confirm: boolean;
}
interface Detail extends Item {
  legal_entity: string; photos: { id: number; name: string; added_at: string; added_by: string | null; can_remove: boolean }[];
  removed: { id: number; removed_at: string; removed_by: string | null; reason: string }[];
  photos_left: number; request_status: string | null; archived_by: string | null; can_unconfirm: boolean; can_void: boolean; can_add_photos: boolean;
}
interface List {
  items: Item[]; total: number; page: number; page_size: number; waiting: number; confirmed: number;
  can_create: boolean; can_confirm: boolean; can_void: boolean;
}
interface Options {
  vehicles: { id: number; label: string; legal_entity_id: number; legal_entity: string }[];
  requests: { id: number; number: number; title: string; status: string }[]; today: string; max_photos: number; what_max: number;
}

const photoUrl = (id: number, thumb = false) => `/api/repair-photos/photos/${id}${thumb ? "?thumb=true" : ""}`;
const modelOf = (label: string) => label.split(" · ")[1] ?? "";
const photos = (n: number) => `${n} ${plural(n, "фото", "фото", "фото")}`;

function useRefresh() {
  const qc = useQueryClient();
  return useCallback((id?: number) => {
    qc.invalidateQueries({ queryKey: ["repair-photos"] });
    if (id) qc.invalidateQueries({ queryKey: ["repair-photo", id] });
  }, [qc]);
}

function StateBadge({ it }: { it: Item }) {
  if (it.archived) return <Badge tone="neutral" icon={Archive}>В архиве</Badge>;
  return it.confirmed ? <Badge tone="success" icon={CheckCircle2}>Подтверждено</Badge> : <Badge tone="warning">Ждёт подтверждения</Badge>;
}

/* ---------------- карточка в списке ---------------- */

function RepairCard({ it, onOpen, onConfirm, busy }: { it: Item; onOpen: () => void; onConfirm: () => void; busy: boolean }) {
  const n = Math.min(it.preview.length, 4);
  return (
    <article className={`card rp-card ${it.confirmed ? "is-ok" : "is-wait"} ${it.archived ? "is-gone" : ""}`}>
      <button className={`rp-thumbs n${n}`} onClick={onOpen} aria-label={`Открыть ремонт ${it.plate} от ${fmtDate(it.repair_date)}`}>
        {it.preview.slice(0, 4).map((id, i) => (
          <span className="rp-th" key={id}>
            <img src={photoUrl(id, true)} alt="" loading="lazy" />
            {i === 3 && it.photo_count > 4 && <span className="rp-more">+{it.photo_count - 4}</span>}
          </span>
        ))}
      </button>
      <div className="rp-body">
        <div className="rp-top">
          <strong className="rp-plate">{it.plate}</strong>
          <span className="muted rp-model">{modelOf(it.vehicle)}</span>
          <span className="rp-date">{fmtDate(it.repair_date)}</span>
        </div>
        <p className="rp-what">{it.what}</p>
        <div className="rp-badges">
          <StateBadge it={it} />
          <Badge tone="neutral" icon={Camera}>{photos(it.photo_count)}</Badge>
          {it.request_number !== null && <Badge tone="info" icon={Wrench}>Обращение № {it.request_number}</Badge>}
        </div>
        <div className="muted small">
          {it.confirmed && it.confirmed_at ? <>Подтвердил(а): {it.confirmed_by ?? "—"}, {fmtDateTime(it.confirmed_at)}<br /></> : null}
          Добавил(а): {it.created_by ?? "—"}, {fmtDateTime(it.created_at)}
        </div>
      </div>
      <div className="rp-actions">
        <Button onClick={onOpen}>Открыть</Button>
        {it.can_confirm && <Button variant="primary" icon={CheckCircle2} loading={busy} onClick={onConfirm}>Подтвердить</Button>}
      </div>
    </article>
  );
}

/* ---------------- добавление ---------------- */

function AddModal({ onClose, canConfirm }: { onClose: () => void; canConfirm: boolean }) {
  const toast = useToast();
  const refresh = useRefresh();
  const entity = useEntityParam();
  const f = useForm({ vehicle_id: "", what: "", repair_date: todayIso(), request_id: "" });
  const [files, setFiles] = useState<File[]>([]);
  const [general, setGeneral] = useState("");
  const camera = useRef<HTMLInputElement>(null);
  const gallery = useRef<HTMLInputElement>(null);
  const opts = useQuery({
    queryKey: ["repair-photos-options", f.values.vehicle_id, entity],
    queryFn: () => get<Options>("/repair-photos/options", { vehicle_id: f.values.vehicle_id || undefined, legal_entity_id: entity }),
    placeholderData: (p) => p,
  });
  const o = opts.data;
  const max = o?.max_photos ?? 20;
  const urls = useMemo(() => files.map((x) => URL.createObjectURL(x)), [files]);
  useEffect(() => () => urls.forEach((u) => URL.revokeObjectURL(u)), [urls]);

  const pick = async (chosen: File[]) => {            // файлы копируются сразу: после сброса поля выбора список браузера пустеет
    const small = await shrinkAll(chosen.slice(0, Math.max(max - files.length, 0)));
    setFiles((old) => [...old, ...small].slice(0, max));
  };
  const send = useMutation({
    mutationFn: async () => {
      if (!f.values.vehicle_id) throw new ApiError(422, "required", "Выберите технику", "vehicle_id");
      if (!f.values.what.trim()) throw new ApiError(422, "required", "Напишите, что отремонтировали", "what");
      if (!files.length) throw new ApiError(422, "no_file", "Сфотографируйте технику после ремонта", "files");
      const form = new FormData();
      form.append("vehicle_id", f.values.vehicle_id);
      form.append("what", f.values.what.trim());
      form.append("repair_date", f.values.repair_date);
      form.append("request_id", f.values.request_id);
      files.forEach((x) => form.append("files", x));
      return upload<{ item: Detail }>("/repair-photos", form);
    },
    onSuccess: () => {
      refresh();
      toast.success(canConfirm ? "Фото ремонта добавлены. Теперь их можно подтвердить" : "Фото ремонта добавлены. Директор увидит их и подтвердит");
      onClose();
    },
    onError: (e) => { f.fail(e); setGeneral(e instanceof ApiError && (e.field || e.fields) ? "" : errorText(e)); },
  });

  return (
    <Modal title="Фото ремонта" onClose={onClose}
      footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={send.isPending} onClick={() => { f.clear(); setGeneral(""); send.mutate(); }}>Сохранить</Button></>}>
      <div className="stack">
        {general && <Alert tone="error">{general}</Alert>}
        <div className="form-grid">
          <Field label="Техника" full error={f.errors.vehicle_id}>
            <select className="input" value={f.values.vehicle_id} onChange={(e) => { f.set("vehicle_id", e.target.value); f.set("request_id", ""); }}>
              <option value="">Выберите технику</option>
              {(o?.vehicles ?? []).map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
            </select>
          </Field>
          <Field label="Дата ремонта" error={f.errors.repair_date}>
            <input className="input" type="date" value={f.values.repair_date} max={o?.today ?? todayIso()} min="2000-01-01" onChange={(e) => f.set("repair_date", e.target.value)} />
          </Field>
          {f.values.vehicle_id && (o?.requests.length ?? 0) > 0 && (
            <Field label="Обращение (по желанию)" error={f.errors.request_id}>
              <select className="input" value={f.values.request_id} onChange={(e) => f.set("request_id", e.target.value)}>
                <option value="">Без обращения</option>
                {o!.requests.map((r) => <option key={r.id} value={r.id}>№ {r.number} · {r.title}</option>)}
              </select>
            </Field>
          )}
          <Field label="Что отремонтировали" full error={f.errors.what} hint="Например: заменили тормозные колодки и прокачали тормоза">
            <textarea className="input" rows={4} value={f.values.what} maxLength={o?.what_max ?? 2000} onChange={(e) => f.set("what", e.target.value)} />
          </Field>
        </div>
        <div className="job-stage">
          <strong>Фото после ремонта</strong>
          {files.length > 0 && (
            <div className="scan-grid job-thumbs">
              {files.map((x, i) => (
                <div className="scan" key={`${x.name}-${i}`}>
                  <img src={urls[i]} alt={x.name} />
                  <button className="scan-x" onClick={() => setFiles((old) => old.filter((_, j) => j !== i))} aria-label="Убрать фото" title="Убрать фото"><X size={14} aria-hidden /></button>
                </div>
              ))}
            </div>
          )}
          {f.errors.files && <span className="err" role="alert" style={{ color: "var(--error)", fontSize: 12 }}>{f.errors.files}</span>}
          <input ref={camera} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { const chosen = Array.from(e.target.files ?? []); e.target.value = ""; pick(chosen); }} />
          <input ref={gallery} type="file" accept="image/*" multiple hidden onChange={(e) => { const chosen = Array.from(e.target.files ?? []); e.target.value = ""; pick(chosen); }} />
          {files.length < max && (
            <div className="row wrap">
              <Button icon={Camera} onClick={() => camera.current?.click()}>{files.length ? "Ещё снимок" : "Сфотографировать"}</Button>
              <Button icon={ImagePlus} onClick={() => gallery.current?.click()}>Выбрать из галереи</Button>
            </div>
          )}
          <span className="muted small">Хотя бы одно фото. Не больше {max}. Можно добавить ещё, пока директор не подтвердил.</span>
        </div>
      </div>
    </Modal>
  );
}

/* ---------------- правка текста ---------------- */

function EditModal({ it, onClose, onSaved }: { it: Detail; onClose: () => void; onSaved: () => void }) {
  const f = useForm({ what: it.what, repair_date: it.repair_date, request_id: it.request_id ? String(it.request_id) : "" });
  const [general, setGeneral] = useState("");
  const opts = useQuery({
    queryKey: ["repair-photos-options", String(it.vehicle_id), "edit"],
    queryFn: () => get<Options>("/repair-photos/options", { vehicle_id: it.vehicle_id }),
    retry: false,
  });
  const o = opts.data;
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { what: f.values.what, repair_date: f.values.repair_date };
      if (f.values.request_id) body.request_id = Number(f.values.request_id);
      else if (it.request_id) body.clear_request = true;
      return patch(`/repair-photos/${it.id}`, body);
    },
    onSuccess: () => { onSaved(); onClose(); },
    onError: (e) => { f.fail(e); setGeneral(e instanceof ApiError && (e.field || e.fields) ? "" : errorText(e)); },
  });
  const requests = o?.requests ?? [];
  return (
    <Modal title="Изменить запись о ремонте" onClose={onClose}
      footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={save.isPending} onClick={() => { f.clear(); setGeneral(""); save.mutate(); }}>Сохранить</Button></>}>
      <div className="stack">
        {general && <Alert tone="error">{general}</Alert>}
        <div className="form-grid">
          <div className="field full"><span className="label">Техника</span><div className="input-static">{it.vehicle}</div></div>
          <Field label="Дата ремонта" error={f.errors.repair_date}>
            <input className="input" type="date" value={f.values.repair_date} max={o?.today ?? todayIso()} min="2000-01-01" onChange={(e) => f.set("repair_date", e.target.value)} />
          </Field>
          {(requests.length > 0 || it.request_id) && (
            <Field label="Обращение" error={f.errors.request_id}>
              <select className="input" value={f.values.request_id} onChange={(e) => f.set("request_id", e.target.value)}>
                <option value="">Без обращения</option>
                {it.request_id && !requests.some((r) => r.id === it.request_id) && <option value={it.request_id}>№ {it.request_number} · {it.request_title}</option>}
                {requests.map((r) => <option key={r.id} value={r.id}>№ {r.number} · {r.title}</option>)}
              </select>
            </Field>
          )}
          <Field label="Что отремонтировали" full error={f.errors.what}>
            <textarea className="input" rows={5} value={f.values.what} maxLength={o?.what_max ?? 2000} onChange={(e) => f.set("what", e.target.value)} />
          </Field>
        </div>
      </div>
    </Modal>
  );
}

/* ---------------- просмотр: фото, описание, подтверждение ---------------- */

function Gallery({ it, idx, setIdx, onRemove }: { it: Detail; idx: number; setIdx: (i: number) => void; onRemove: () => void }) {
  const list = it.photos;
  const cur = list[Math.min(idx, list.length - 1)];
  const last = list.length - 1;
  const go = useCallback((d: number) => setIdx(Math.min(Math.max(idx + d, 0), last)), [idx, last, setIdx]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight") go(1);
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [go]);
  if (!cur) return <Alert tone="warning">У этой записи нет фото</Alert>;
  return (
    <div className="stack-sm">
      <div className="rp-viewer">
        <img src={photoUrl(cur.id)} alt={`Фото ${idx + 1} из ${list.length}: ${it.plate}`} />
        {list.length > 1 && (
          <>
            <button className="rp-nav prev" onClick={() => go(-1)} disabled={idx <= 0} aria-label="Предыдущее фото"><ChevronLeft size={22} aria-hidden /></button>
            <button className="rp-nav next" onClick={() => go(1)} disabled={idx >= last} aria-label="Следующее фото"><ChevronRight size={22} aria-hidden /></button>
          </>
        )}
        <span className="rp-count">{idx + 1} из {list.length}</span>
      </div>
      {list.length > 1 && (
        <div className="rp-strip" role="group" aria-label="Все фото">
          {list.map((ph, i) => (
            <button key={ph.id} className={`rp-strip-item ${i === idx ? "on" : ""}`} onClick={() => setIdx(i)} aria-label={`Фото ${i + 1}`} aria-current={i === idx}>
              <img src={photoUrl(ph.id, true)} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      )}
      <div className="row wrap small muted">
        <span>Снято: {cur.added_by ?? "—"}, {fmtDateTime(cur.added_at)}</span>
        <a href={photoUrl(cur.id)} target="_blank" rel="noreferrer"><ExternalLink size={13} aria-hidden /> Открыть отдельно</a>
        {cur.can_remove && <button className="linklike" onClick={onRemove}>Убрать это фото</button>}
      </div>
    </div>
  );
}

function DetailModal({ id, onClose }: { id: number; onClose: () => void }) {
  const toast = useToast();
  const refresh = useRefresh();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["repair-photo", id], queryFn: () => get<{ item: Detail }>(`/repair-photos/${id}`) });
  const it = q.data?.item;
  const [idx, setIdx] = useState(0);
  const [ask, setAsk] = useState<null | "edit" | "unconfirm" | "void" | "remove">(null);
  const input = useRef<HTMLInputElement>(null);
  const closeAsk = useCallback(() => setAsk(null), []);
  const done = (r: { item: Detail }, text?: string) => {
    qc.setQueryData(["repair-photo", id], r);
    refresh();
    if (text) toast.success(text);
  };
  const confirm = useMutation({
    mutationFn: () => post<{ item: Detail }>(`/repair-photos/${id}/confirm`),
    onSuccess: (r) => done(r, "Ремонт подтверждён"),
    onError: (e) => toast.error(errorText(e)),
  });
  const restore = useMutation({
    mutationFn: () => post<{ item: Detail }>(`/repair-photos/${id}/restore`),
    onSuccess: (r) => done(r, "Запись возвращена из архива"),
    onError: (e) => toast.error(errorText(e)),
  });
  const add = useMutation({
    mutationFn: async (files: File[]) => { const f = new FormData(); (await shrinkAll(files)).forEach((x) => f.append("files", x)); return upload<{ item: Detail }>(`/repair-photos/${id}/photos`, f); },
    onSuccess: (r) => { done(r, "Фото добавлены"); setIdx(r.item.photos.length - 1); },
    onError: (e) => toast.error(errorText(e)),
  });
  const current = it?.photos[Math.min(idx, (it?.photos.length ?? 1) - 1)];

  return (
    <Modal title={it ? `Ремонт: ${it.plate}` : "Фото ремонта"} wide onClose={onClose}
      footer={it ? (
        <div className="rp-foot">
          {it.can_confirm && <Button variant="primary" icon={CheckCircle2} loading={confirm.isPending} onClick={() => confirm.mutate()}>Подтвердить</Button>}
          <Button className="rp-close" onClick={onClose}>Закрыть</Button>
        </div>
      ) : undefined}>
      <QueryState q={q} what="Запись">
        {it && (
          <div className="stack">
            {it.archived && <Alert tone="warning">В архиве{it.archived_by ? ` (убрал(а): ${it.archived_by})` : ""}. Причина: {it.archive_reason || "не указана"}</Alert>}
            {it.confirmed
              ? <Alert tone="success">Подтверждено: {it.confirmed_by ?? "—"}, {fmtDateTime(it.confirmed_at)}. Менять запись нельзя, пока директор не снимет подтверждение.</Alert>
              : !it.archived && <Alert tone="warning">Ждёт подтверждения директора.</Alert>}
            <dl className="rp-facts">
              <dt>Что отремонтировали</dt><dd className="rp-text"><strong>{it.what}</strong></dd>
              <dt>Техника</dt><dd><Link to={`/vehicles/${it.vehicle_id}`}>{it.vehicle}</Link></dd>
              <dt>Дата ремонта</dt><dd>{fmtDate(it.repair_date)}</dd>
              {it.request_id !== null && <><dt>Обращение</dt><dd><Link to={`/requests/${it.request_id}`}>№ {it.request_number} · {it.request_title}</Link>{it.request_status && <span className="muted"> ({it.request_status.toLowerCase()})</span>}</dd></>}
              <dt>Добавил(а)</dt><dd>{it.created_by ?? "—"}, {fmtDateTime(it.created_at)}</dd>
            </dl>
            {(it.can_edit || it.can_add_photos || it.can_unconfirm || it.can_void) && (
              <div className="rp-tools">
                {it.can_unconfirm && <Button size="sm" icon={Undo2} onClick={() => setAsk("unconfirm")}>Снять подтверждение</Button>}
                {it.can_edit && <Button size="sm" icon={Pencil} onClick={() => setAsk("edit")}>Изменить</Button>}
                {it.can_add_photos && (
                  <>
                    <input ref={input} type="file" accept="image/*" multiple hidden onChange={(e) => { const files = Array.from(e.target.files ?? []); e.target.value = ""; if (files.length) add.mutate(files); }} />
                    <Button size="sm" icon={Camera} loading={add.isPending} onClick={() => input.current?.click()}>Добавить фото</Button>
                  </>
                )}
                {it.can_void && !it.archived && <Button size="sm" variant="ghost" icon={Archive} onClick={() => setAsk("void")}>В архив</Button>}
                {it.can_void && it.archived && <Button size="sm" icon={ArchiveRestore} loading={restore.isPending} onClick={() => restore.mutate()}>Вернуть из архива</Button>}
              </div>
            )}
            <Gallery it={it} idx={idx} setIdx={setIdx} onRemove={() => setAsk("remove")} />
            {it.removed.length > 0 && (
              <div className="muted small">
                <strong>Убранные фото ({it.removed.length}):</strong>
                <ul className="rp-removed">
                  {it.removed.map((r) => <li key={r.id}>{fmtDateTime(r.removed_at)}, {r.removed_by ?? "—"}: {r.reason}</li>)}
                </ul>
              </div>
            )}
          </div>
        )}
      </QueryState>
      {it && ask === "edit" && <EditModal it={it} onClose={closeAsk} onSaved={() => { refresh(id); toast.success("Сохранено"); }} />}
      {it && ask === "unconfirm" && (
        <ConfirmModal title="Снять подтверждение" confirmLabel="Снять" reasonLabel="Причина" reasonRequired={false}
          text="Запись снова можно будет менять, а в списке она вернётся в «Ждут подтверждения». Причину можно не писать, но она сохранится в журнале."
          onConfirm={async (reason) => done(await post<{ item: Detail }>(`/repair-photos/${id}/unconfirm`, { reason }), "Подтверждение снято")} onClose={closeAsk} />
      )}
      {it && ask === "void" && (
        <ConfirmModal title="Убрать в архив" danger confirmLabel="В архив" reasonLabel="Причина"
          text="Запись пропадёт из истории, но останется в архиве вместе с фото. Её можно вернуть."
          onConfirm={async (reason) => { done(await post<{ item: Detail }>(`/repair-photos/${id}/void`, { reason }), "Запись убрана в архив"); }} onClose={closeAsk} />
      )}
      {it && current && ask === "remove" && (
        <ConfirmModal title="Убрать фото" danger confirmLabel="Убрать" reasonLabel="Причина" reasonRequired={false}
          text="Фото пропадёт из записи, но останется в журнале. Причину можно не писать."
          onConfirm={async (reason) => { done(await post<{ item: Detail }>(`/repair-photos/photos/${current.id}/remove`, { reason }), "Фото убрано"); setIdx(0); }} onClose={closeAsk} />
      )}
    </Modal>
  );
}

/* ---------------- страница ---------------- */

export function RepairPhotos() {
  const { can } = useAuth();
  const toast = useToast();
  const refresh = useRefresh();
  const entity = useEntityParam();
  const [s, setS] = useUrlState({ q: "", state: "", from: "", to: "", archived: "", page: "" });
  const [sp, setSp] = useSearchParams();
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps

  // ссылка из уведомления (?open=12) открывает карточку сразу; обычное открытие из списка адрес не меняет
  const [openId, setOpenId] = useState(Number(sp.get("open")) || 0);
  const fromLink = Number(sp.get("open")) || 0;
  useEffect(() => { if (fromLink) setOpenId(fromLink); }, [fromLink]);
  const closeOpen = useCallback(() => {
    setOpenId(0);
    setSp((old) => { const n = new URLSearchParams(old); n.delete("open"); return n; }, { replace: true });
  }, [setSp]);
  const [adding, setAdding] = useState(false);
  const closeAdd = useCallback(() => setAdding(false), []);

  const page = Number(s.page) || 1;
  const q = useQuery({
    queryKey: ["repair-photos", entity, s.q, s.state, s.from, s.to, s.archived, page],
    queryFn: () => get<List>("/repair-photos", {
      legal_entity_id: entity, q: s.q || undefined, state: s.state || undefined, date_from: s.from || undefined, date_to: s.to || undefined,
      archived: s.archived ? true : undefined, page, page_size: 24,
    }),
    placeholderData: (p) => p,
    refetchInterval: 60_000,
  });
  const d = q.data;
  const [busyId, setBusyId] = useState(0);
  const confirm = useMutation({
    mutationFn: (id: number) => { setBusyId(id); return post(`/repair-photos/${id}/confirm`); },
    onSuccess: (_r, id) => { refresh(id); toast.success("Ремонт подтверждён"); },
    onError: (e) => toast.error(errorText(e)),
    onSettled: () => setBusyId(0),
  });
  const filtered = !!(s.q || s.from || s.to);
  const archivedView = !!s.archived;

  return (
    <>
      <PageHeader
        title="Фото ремонта"
        sub="Что отремонтировали и как выглядит техника после ремонта. Вся история хранится, любой ремонт можно открыть и посмотреть"
        actions={can("repair_photos.create") ? <Button variant="primary" icon={Camera} onClick={() => setAdding(true)}>Добавить фото ремонта</Button> : undefined}
      />
      <QueryState q={q}>
        {d && (
          <div className="stack">
            {d.can_confirm && d.waiting > 0 && s.state !== "waiting" && !archivedView && (
              <Alert tone="warning">
                <span className="row wrap">
                  <span>Ждут подтверждения: <strong>{d.waiting}</strong></span>
                  <Button size="sm" onClick={() => setS({ state: "waiting" })}>Показать</Button>
                </span>
              </Alert>
            )}
            <div className="toolbar">
              <div className="search-box">
                <Search size={16} aria-hidden />
                <input className="input" placeholder="Госномер, марка или что отремонтировали" aria-label="Поиск" value={text} onChange={(e) => setText(e.target.value)} />
              </div>
              <label className="rp-date-pick"><span className="muted small">с</span>
                <input className="input compact" type="date" value={s.from} max={s.to || undefined} onChange={(e) => setS({ from: e.target.value })} aria-label="Ремонты с даты" />
              </label>
              <label className="rp-date-pick"><span className="muted small">по</span>
                <input className="input compact" type="date" value={s.to} min={s.from || undefined} onChange={(e) => setS({ to: e.target.value })} aria-label="Ремонты по дату" />
              </label>
              {d.can_void && (
                <label className="check"><input type="checkbox" checked={archivedView} onChange={(e) => setS({ archived: e.target.checked ? "1" : "" })} /> Архив</label>
              )}
            </div>
            <FilterChips value={s.state as "" | "waiting" | "confirmed"} onChange={(v) => setS({ state: v })} options={[
              { id: "", label: "Все", count: d.waiting + d.confirmed },
              { id: "waiting", label: "Ждут подтверждения", count: d.waiting },
              { id: "confirmed", label: "Подтверждено", count: d.confirmed },
            ]} />
            {d.items.length === 0 ? (
              <Card>
                <Empty icon={Camera} title={archivedView ? "В архиве пусто" : filtered || s.state ? "Ничего не найдено" : "Пока нет фото ремонта"}>
                  {archivedView ? "Сюда попадают записи, которые убрали в архив."
                    : filtered || s.state ? "Измените поиск или даты."
                    : d.can_create ? "Нажмите «Добавить фото ремонта»: выберите технику, напишите, что отремонтировали, и сфотографируйте результат."
                    : "Когда механик отфотографирует технику после ремонта, записи появятся здесь."}
                </Empty>
              </Card>
            ) : (
              <>
                <div className="rp-grid">
                  {d.items.map((it) => (
                    <RepairCard key={it.id} it={it} busy={busyId === it.id} onOpen={() => setOpenId(it.id)} onConfirm={() => confirm.mutate(it.id)} />
                  ))}
                </div>
                <Pagination page={d.page} pageSize={d.page_size} total={d.total} onPage={(p) => setS({ page: String(p) })} />
              </>
            )}
          </div>
        )}
      </QueryState>
      {adding && <AddModal onClose={closeAdd} canConfirm={!!d?.can_confirm} />}
      {openId > 0 && <DetailModal id={openId} onClose={closeOpen} />}
    </>
  );
}
