// Общие части личных дел: сроки документов, превью сканов, окна загрузки и правки. Нужны и водителям, и технике.
import { ReactNode, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, Archive, CheckCircle2, Clock, Download, ExternalLink, FileText, ImagePlus, Pencil, Plus, RotateCcw, Truck, Upload, User as UserIcon, X,
} from "lucide-react";
import { patch, post, upload } from "../../api";
import { fmtDate, fmtSize, plural } from "../../format";
import { Alert, Badge, Button, ConfirmModal, Field, Modal, useForm, useToast } from "../../ui";

export interface DocFile {
  link_id: number; file_id: number; name: string; content_type: string; size_bytes: number; is_image: boolean;
  width: number | null; height: number | null; added_at: string;
}
export type Expiry = "none" | "ok" | "expiring" | "expired";
export interface Doc {
  id: number; kind: string; kind_label: string; title: string; doc_number: string; issued_on: string | null; valid_until: string | null;
  expiry: Expiry; days_left: number | null; issued_by: string; note: string; archived: boolean; archive_reason: string; archived_at: string | null;
  created_at: string; files: DocFile[]; removed_files: number;
}
export interface Kind { code: string; label: string; sensitive?: boolean; expires: boolean }
export interface Summary { documents: number; expired: number; expiring: number; nearest_until: string | null }
export type Owner = { type: "employees" | "vehicles"; id: number };

export const MAX_FILE_MB = 10;
export const MAX_FILES = 10;
export const ACCEPT = "image/jpeg,image/png,image/webp,application/pdf,.jpg,.jpeg,.png,.webp,.pdf";

export const fileUrl = (id: number, opts: { thumb?: boolean; download?: boolean } = {}) =>
  `/api/dossier/files/${id}${opts.thumb ? "?thumb=true" : opts.download ? "?download=true" : ""}`;

/* ---------------- сроки ---------------- */

const days = (n: number) => `${n} ${plural(n, "день", "дня", "дней")}`;

export function ExpiryBadge({ doc }: { doc: Pick<Doc, "expiry" | "days_left" | "valid_until" | "kind"> }) {
  if (doc.kind === "photo") return null;
  if (doc.expiry === "expired") return <Badge tone="error" icon={AlertTriangle}>просрочен {days(Math.abs(doc.days_left ?? 0))}</Badge>;
  if (doc.expiry === "expiring")
    return <Badge tone="warning" icon={Clock}>{doc.days_left === 0 ? "кончается сегодня" : `осталось ${days(doc.days_left ?? 0)}`}</Badge>;
  if (doc.expiry === "ok") return <Badge tone="success" icon={CheckCircle2}>до {fmtDate(doc.valid_until)}</Badge>;
  return <Badge>срок не указан</Badge>;
}

/** Короткая сводка по личному делу для списков. */
export function SummaryBadges({ s }: { s: Summary }) {
  if (s.expired === 0 && s.expiring === 0)
    return s.documents > 0 ? <Badge tone="success" icon={CheckCircle2}>в порядке</Badge> : <Badge>нет документов</Badge>;
  return (
    <span className="row wrap" style={{ gap: 6 }}>
      {s.expired > 0 && <Badge tone="error" icon={AlertTriangle}>просрочено: {s.expired}</Badge>}
      {s.expiring > 0 && <Badge tone="warning" icon={Clock}>скоро: {s.expiring}</Badge>}
    </span>
  );
}

/* ---------------- фото и сканы ---------------- */

export function Avatar({ fileId, kind, size = 40 }: { fileId: number | null; kind: "person" | "vehicle"; size?: number }) {
  const [broken, setBroken] = useState(false);
  const Icon = kind === "person" ? UserIcon : Truck;
  return (
    <span className={`avatar ${kind}`} style={{ width: size, height: size }}>
      {fileId && !broken ? <img src={fileUrl(fileId, { thumb: true })} alt="" loading="lazy" onError={() => setBroken(true)} /> : <Icon size={Math.round(size * 0.45)} aria-hidden />}
    </span>
  );
}

function Viewer({ file, onClose }: { file: DocFile; onClose: () => void }) {
  return (
    <Modal title={file.name} wide onClose={onClose} footer={<>
      <a className="btn" href={fileUrl(file.file_id, { download: true })}><Download size={16} aria-hidden /> Скачать</a>
      <a className="btn" href={fileUrl(file.file_id)} target="_blank" rel="noreferrer"><ExternalLink size={16} aria-hidden /> Открыть отдельно</a>
      <Button variant="primary" onClick={onClose}>Закрыть</Button>
    </>}>
      <div className="viewer"><img src={fileUrl(file.file_id)} alt={file.name} /></div>
    </Modal>
  );
}

export function ScanGrid({ files, onRemove }: { files: DocFile[]; onRemove?: (f: DocFile) => void }) {
  const [view, setView] = useState<DocFile | null>(null);
  if (files.length === 0) return <div className="muted small">Сканов нет</div>;
  return (
    <>
      <div className="scan-grid">
        {files.map((f) => (
          <div className="scan" key={f.link_id}>
            {f.is_image ? (
              <button className="scan-open" onClick={() => setView(f)} aria-label={`Открыть: ${f.name}`}>
                <img src={fileUrl(f.file_id, { thumb: true })} alt="" loading="lazy" />
              </button>
            ) : (
              <a className="scan-open pdf" href={fileUrl(f.file_id)} target="_blank" rel="noreferrer" aria-label={`Открыть PDF: ${f.name}`}>
                <FileText size={30} aria-hidden />
                <span>PDF</span>
              </a>
            )}
            <div className="scan-cap" title={f.name}><span>{f.name}</span><span className="muted">{fmtSize(f.size_bytes)}</span></div>
            {onRemove && (
              <button className="scan-x" onClick={() => onRemove(f)} aria-label={`Убрать скан ${f.name}`} title="Убрать скан"><X size={14} aria-hidden /></button>
            )}
          </div>
        ))}
      </div>
      {view && <Viewer file={view} onClose={() => setView(null)} />}
    </>
  );
}

/* ---------------- выбор файлов ---------------- */

function problemWith(files: File[], imagesOnly: boolean): string {
  if (files.length > MAX_FILES) return `За один раз можно загрузить не больше ${MAX_FILES} файлов`;
  const big = files.find((f) => f.size > MAX_FILE_MB * 1024 * 1024);
  if (big) return `Файл «${big.name}» больше ${MAX_FILE_MB} МБ: уменьшите или сожмите его`;
  const empty = files.find((f) => f.size === 0);
  if (empty) return `Файл «${empty.name}» пустой`;
  if (imagesOnly) {
    const notImage = files.find((f) => f.type && !f.type.startsWith("image/"));
    if (notImage) return `Для фото нужна картинка, а «${notImage.name}» не картинка`;
  }
  return "";
}

function FilePicker({ files, onChange, imagesOnly, error, multiple = true }: { files: File[]; onChange: (f: File[]) => void; imagesOnly: boolean; error?: string; multiple?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  const local = problemWith(files, imagesOnly);
  const shown = error || local;
  return (
    <div className="stack-sm">
      <input ref={ref} type="file" className="sr-only" tabIndex={-1} multiple={multiple} accept={imagesOnly ? "image/jpeg,image/png,image/webp" : ACCEPT}
        onChange={(e) => { const picked = Array.from(e.target.files ?? []); onChange(multiple ? [...files, ...picked] : picked.slice(0, 1)); e.target.value = ""; }} />
      <div className="row wrap">
        <Button icon={imagesOnly ? ImagePlus : Upload} onClick={() => ref.current?.click()}>{files.length ? "Добавить ещё" : imagesOnly ? "Выбрать фото" : "Выбрать файлы"}</Button>
        <span className="muted small">{imagesOnly ? "JPG, PNG или WebP" : "Фото (JPG, PNG, WebP) или PDF"}, до {MAX_FILE_MB} МБ каждый</span>
      </div>
      {files.length > 0 && (
        <ul className="picked">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`}>
              <FileText size={15} aria-hidden />
              <span className="name">{f.name}</span>
              <span className="muted small">{fmtSize(f.size)}</span>
              <button className="btn ghost sm" onClick={() => onChange(files.filter((_, j) => j !== i))} aria-label={`Убрать ${f.name}`}><X size={14} aria-hidden /></button>
            </li>
          ))}
        </ul>
      )}
      {shown && <span className="err small" role="alert" style={{ color: "var(--error)" }}>{shown}</span>}
    </div>
  );
}

/* ---------------- добавление документа ---------------- */

const nul = (s: string) => (s.trim() === "" ? null : s);

export function NewDocModal({ owner, kinds, kind, onClose }: { owner: Owner; kinds: Kind[]; kind: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ kind, title: "", doc_number: "", issued_on: "", valid_until: "", issued_by: "", note: "" });
  const [files, setFiles] = useState<File[]>([]);
  const k = kinds.find((x) => x.code === f.values.kind);
  const isPhoto = f.values.kind === "photo";
  const m = useMutation({
    mutationFn: () => {
      const v = f.values;
      const body = new FormData();
      body.set("kind", v.kind);
      body.set("title", v.title);
      body.set("doc_number", v.doc_number);
      body.set("issued_by", v.issued_by);
      body.set("note", v.note);
      if (!isPhoto) {
        const a = nul(v.issued_on), b = nul(v.valid_until);
        if (a) body.set("issued_on", a);
        if (b) body.set("valid_until", b);
      }
      files.forEach((x) => body.append("files", x));
      return upload<Doc>(`/dossier/${owner.type}/${owner.id}/docs`, body);
    },
    onSuccess: () => { qc.invalidateQueries(); toast.success(isPhoto ? "Фото добавлено" : "Документ добавлен"); onClose(); },
    onError: f.fail,
  });
  const clientProblem = problemWith(files, isPhoto);
  const canSend = !clientProblem && (!isPhoto || files.length > 0);
  return (
    <Modal title={isPhoto ? "Добавить фото" : "Добавить документ"} wide onClose={onClose} footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" icon={Upload} loading={m.isPending} disabled={!canSend} onClick={() => { f.clear(); m.mutate(); }}>Сохранить</Button>
    </>}>
      <div className="stack">
        {f.formError && !f.errors.files && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          <Field label="Что это за документ" error={f.errors.kind}>
            <select className="input" value={f.values.kind} onChange={(e) => f.set("kind", e.target.value)}>
              {kinds.map((x) => <option key={x.code} value={x.code}>{x.label}</option>)}
            </select>
          </Field>
          {!isPhoto && <Field label="Номер" error={f.errors.doc_number} hint="Серия и номер, если есть"><input className="input" value={f.values.doc_number} maxLength={60} onChange={(e) => f.set("doc_number", e.target.value)} /></Field>}
          {!isPhoto && <Field label="Когда выдан" error={f.errors.issued_on}><input className="input" type="date" value={f.values.issued_on} onChange={(e) => f.set("issued_on", e.target.value)} /></Field>}
          {!isPhoto && (
            <Field label="Действует до" error={f.errors.valid_until} hint={k?.expires === false ? undefined : "По этой дате программа напомнит о замене"}>
              <input className="input" type="date" value={f.values.valid_until} onChange={(e) => f.set("valid_until", e.target.value)} />
            </Field>
          )}
          {!isPhoto && <Field label="Кем выдан" full error={f.errors.issued_by}><input className="input" value={f.values.issued_by} maxLength={200} onChange={(e) => f.set("issued_by", e.target.value)} /></Field>}
          <Field label="Название" full error={f.errors.title} hint="Можно не заполнять: подставится вид документа"><input className="input" value={f.values.title} maxLength={200} onChange={(e) => f.set("title", e.target.value)} /></Field>
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} maxLength={2000} onChange={(e) => f.set("note", e.target.value)} /></Field>
        </div>
        <div className="stack-sm">
          <strong className="small">{isPhoto ? "Фото" : "Сканы и фотографии документа"}</strong>
          <FilePicker files={files} onChange={setFiles} imagesOnly={isPhoto} error={f.errors.files} />
          {!isPhoto && files.length === 0 && <span className="muted small">Скан можно приложить и позже. Для фото нужен файл сразу.</span>}
        </div>
      </div>
    </Modal>
  );
}

/* ---------------- правка полей документа ---------------- */

function EditDocModal({ doc, onClose }: { doc: Doc; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const isPhoto = doc.kind === "photo";
  const f = useForm({ title: doc.title, doc_number: doc.doc_number, issued_on: doc.issued_on ?? "", valid_until: doc.valid_until ?? "", issued_by: doc.issued_by, note: doc.note });
  const m = useMutation({
    mutationFn: () => patch(`/dossier/docs/${doc.id}`, { ...f.values, issued_on: nul(f.values.issued_on), valid_until: nul(f.values.valid_until) }),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Сохранено"); onClose(); },
    onError: f.fail,
  });
  return (
    <Modal title={`Изменить: ${doc.kind_label}`} wide onClose={onClose} footer={<>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Сохранить</Button>
    </>}>
      <div className="stack">
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
        <div className="form-grid">
          <Field label="Название" full error={f.errors.title}><input className="input" value={f.values.title} maxLength={200} onChange={(e) => f.set("title", e.target.value)} /></Field>
          {!isPhoto && <Field label="Номер" error={f.errors.doc_number}><input className="input" value={f.values.doc_number} maxLength={60} onChange={(e) => f.set("doc_number", e.target.value)} /></Field>}
          {!isPhoto && <Field label="Кем выдан" error={f.errors.issued_by}><input className="input" value={f.values.issued_by} maxLength={200} onChange={(e) => f.set("issued_by", e.target.value)} /></Field>}
          {!isPhoto && <Field label="Когда выдан" error={f.errors.issued_on}><input className="input" type="date" value={f.values.issued_on} onChange={(e) => f.set("issued_on", e.target.value)} /></Field>}
          {!isPhoto && <Field label="Действует до" error={f.errors.valid_until}><input className="input" type="date" value={f.values.valid_until} onChange={(e) => f.set("valid_until", e.target.value)} /></Field>}
          <Field label="Заметка" full error={f.errors.note}><textarea className="input" value={f.values.note} maxLength={2000} onChange={(e) => f.set("note", e.target.value)} /></Field>
        </div>
      </div>
    </Modal>
  );
}

/* ---------------- карточка одного документа ---------------- */

export function DocCard({ doc, canEdit }: { doc: Doc; canEdit: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const pick = useRef<HTMLInputElement>(null);
  const [sub, setSub] = useState<null | "edit" | "archive" | { remove: DocFile }>(null);
  const isPhoto = doc.kind === "photo";
  const addFiles = useMutation({
    mutationFn: (files: File[]) => {
      const body = new FormData();
      files.forEach((x) => body.append("files", x));
      return upload<Doc>(`/dossier/docs/${doc.id}/files`, body);
    },
    onSuccess: () => { qc.invalidateQueries(); toast.success("Скан добавлен"); },
    onError: (e: any) => toast.error(e.message),
  });
  const restore = useMutation({
    mutationFn: () => post(`/dossier/docs/${doc.id}/restore`),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Документ возвращён"); },
    onError: (e: any) => toast.error(e.message),
  });
  const onPick = (list: FileList | null) => {
    const files = Array.from(list ?? []);
    if (!files.length) return;
    const bad = problemWith(files, isPhoto);
    if (bad) return toast.error(bad);
    addFiles.mutate(files);
  };
  return (
    <article className={`doc ${doc.archived ? "archived" : ""} ${doc.expiry}`}>
      <header className="doc-head">
        <div style={{ minWidth: 0 }}>
          {doc.title !== doc.kind_label ? (
            <><strong>{doc.title}</strong>{doc.doc_number && <span className="muted"> · № {doc.doc_number}</span>}</>
          ) : doc.doc_number ? (
            <strong>№ {doc.doc_number}</strong>
          ) : (
            <span className="muted">{doc.archived ? `${doc.kind_label}, ` : ""}добавлено {fmtDate(doc.created_at)}</span>
          )}
        </div>
        {doc.archived ? <Badge icon={Archive}>в архиве</Badge> : <ExpiryBadge doc={doc} />}
      </header>
      {!isPhoto && (doc.issued_on || doc.issued_by || doc.valid_until) && (
        <dl className="kv compact small">
          {doc.issued_on && <><dt>Выдан</dt><dd>{fmtDate(doc.issued_on)}</dd></>}
          {doc.issued_by && <><dt>Кем</dt><dd>{doc.issued_by}</dd></>}
          {doc.valid_until && <><dt>Действует до</dt><dd>{fmtDate(doc.valid_until)}</dd></>}
        </dl>
      )}
      {doc.note && <div className="small" style={{ whiteSpace: "pre-wrap" }}>{doc.note}</div>}
      {doc.archived && doc.archive_reason && <div className="small muted">Причина архива: {doc.archive_reason}</div>}
      <ScanGrid files={doc.files} onRemove={canEdit && !doc.archived ? (file) => setSub({ remove: file }) : undefined} />
      {doc.removed_files > 0 && <div className="muted small">Убранных сканов: {doc.removed_files} (они остаются в базе и в журнале)</div>}
      {canEdit && (
        <div className="row wrap">
          {doc.archived ? (
            <Button size="sm" icon={RotateCcw} loading={restore.isPending} onClick={() => restore.mutate()}>Вернуть из архива</Button>
          ) : (
            <>
              <input ref={pick} type="file" className="sr-only" tabIndex={-1} multiple={!isPhoto} accept={isPhoto ? "image/jpeg,image/png,image/webp" : ACCEPT}
                onChange={(e) => { onPick(e.target.files); e.target.value = ""; }} />
              <Button size="sm" icon={Plus} loading={addFiles.isPending} onClick={() => pick.current?.click()}>{isPhoto ? "Ещё фото" : "Добавить скан"}</Button>
              <Button size="sm" icon={Pencil} onClick={() => setSub("edit")}>Изменить</Button>
              <Button size="sm" variant="ghost" icon={Archive} onClick={() => setSub("archive")}>В архив</Button>
            </>
          )}
        </div>
      )}
      {sub === "edit" && <EditDocModal doc={doc} onClose={() => setSub(null)} />}
      {sub === "archive" && (
        <ConfirmModal title="Убрать документ в архив?" confirmLabel="В архив" danger reasonLabel="Причина (например, получили новый)"
          text="Документ и его сканы не удаляются, их можно вернуть из архива."
          onConfirm={async (reason) => { await post(`/dossier/docs/${doc.id}/archive`, { reason }); qc.invalidateQueries(); toast.success("Документ в архиве"); }} onClose={() => setSub(null)} />
      )}
      {typeof sub === "object" && sub && "remove" in sub && (
        <ConfirmModal title="Убрать скан из документа?" confirmLabel="Убрать" danger reasonLabel="Причина (например, неверный файл)"
          text={`Файл «${sub.remove.name}» перестанет показываться, но останется в базе и в журнале действий.`}
          onConfirm={async (reason) => { await post(`/dossier/docs/${doc.id}/files/${(sub as { remove: DocFile }).remove.link_id}/remove`, { reason }); qc.invalidateQueries(); toast.success("Скан убран"); }}
          onClose={() => setSub(null)} />
      )}
    </article>
  );
}

/* ---------------- блок «Документы» личного дела ---------------- */

/** Комплект: какие документы уже есть, каких нет. Фото и «другое» в комплект не входят. */
export function Checklist({ kinds, docs }: { kinds: Kind[]; docs: Doc[] }) {
  const need = kinds.filter((k) => k.code !== "other" && k.code !== "photo");
  return (
    <div className="chips" aria-label="Комплект документов">
      {need.map((k) => {
        const mine = docs.filter((d) => d.kind === k.code);
        const worst = mine.some((d) => d.expiry === "expired") ? "error" : mine.some((d) => d.expiry === "expiring") ? "warning" : mine.length ? "success" : "neutral";
        return (
          <Badge key={k.code} tone={worst} icon={mine.length ? (worst === "success" ? CheckCircle2 : AlertTriangle) : undefined}>
            {k.label}{mine.length === 0 ? ": нет" : ""}
          </Badge>
        );
      })}
    </div>
  );
}

export function DocsBlock({ owner, kinds, docs, archived, canEdit, header }: {
  owner: Owner; kinds: Kind[]; docs: Doc[]; archived: Doc[]; canEdit: boolean; header?: ReactNode;
}) {
  const [adding, setAdding] = useState<string | null>(null);
  return (
    <div className="stack">
      {header}
      {canEdit && (
        <div className="row wrap">
          <Button variant="primary" icon={Plus} onClick={() => setAdding(kinds.find((k) => k.code !== "photo")?.code ?? "other")}>Добавить документ</Button>
          <Button icon={ImagePlus} onClick={() => setAdding("photo")}>Добавить фото</Button>
        </div>
      )}
      {kinds.map((k) => {
        const mine = docs.filter((d) => d.kind === k.code);
        if (mine.length === 0 && k.code === "other") return null;
        return (
          <section key={k.code} className="kind-block">
            <div className="row">
              <h3>{k.label}</h3>
              <span className="spacer" />
              {canEdit && <Button size="sm" variant="ghost" icon={Plus} onClick={() => setAdding(k.code)}>Добавить</Button>}
            </div>
            {mine.length === 0 ? <div className="muted small">Пока нет</div> : <div className="doc-list">{mine.map((d) => <DocCard key={d.id} doc={d} canEdit={canEdit} />)}</div>}
          </section>
        );
      })}
      {archived.length > 0 && (
        <details className="archive-box">
          <summary>Архив документов ({archived.length})</summary>
          <div className="doc-list" style={{ marginTop: 12 }}>{archived.map((d) => <DocCard key={d.id} doc={d} canEdit={canEdit} />)}</div>
        </details>
      )}
      {adding && <NewDocModal owner={owner} kinds={kinds} kind={adding} onClose={() => setAdding(null)} />}
    </div>
  );
}
