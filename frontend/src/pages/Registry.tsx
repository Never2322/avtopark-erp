// Один экран для всех «учётных» разделов (справочники, штрафы, аренда, касса…). Поля и правила приходят с сервера (/meta).
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArchiveRestore, Archive, Camera, Download, Plus, Search, X, type LucideIcon, Inbox } from "lucide-react";
import { get, patch, post, qs, upload } from "../api";
import { shrinkAll } from "../images";
import { WorklogPreview } from "../components/WorklogPreview";
import { useAuth } from "../auth";
import { fmtDate, fmtDateTime, fmtMoney, fmtNum, todayIso } from "../format";
import { Page, useDebounced, useEntityParam } from "../hooks";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, FilterChips, Modal, PageHeader, Pagination, QueryState, Table, Tone, useForm, useToast, type Column } from "../ui";

interface FieldMeta {
  name: string; label: string; kind: string; required: boolean; ref: string | null; hint: string; options: { value: string; label: string }[] | null;
  list: boolean; filter: boolean; sum: boolean; locked: boolean; wide: boolean; show_if: [string, string] | null; suggest: boolean; max: number; default: unknown; more?: boolean; places?: number;
}
export interface Meta {
  key: string; title: string; one: string; scope: "entity" | "global"; entity_from: string | null; date_field: string | null;
  states: { value: string; label: string }[]; fields: FieldMeta[]; columns: { name: string; label: string; kind: string; sum: boolean; first: boolean }[];
  entities: { id: number; name: string }[]; suggestions: Record<string, string[]>;
  can: { view: boolean; create: boolean; edit: boolean; void: boolean };
  photos?: { kind: "trip" | "fuel" | "ticket"; label: string } | null;
}
export type Row = Record<string, any> & { id: number; can_edit: boolean; can_void: boolean; archived: boolean; archive_reason: string; legal_entity?: string; legal_entity_id?: number };
interface Listing extends Page<Row> { totals: Record<string, number> }

export function cell(f: { name: string; kind: string; options?: FieldMeta["options"]; places?: number }, r: Row) {
  const v = r[f.name];
  if (v === null || v === undefined || v === "") return <span className="muted">—</span>;
  switch (f.kind) {
    case "money": return <span className="nowrap">{fmtMoney(v)}</span>;
    case "decimal": return fmtNum(v, (f.places ?? 0) >= 3 ? 2 : 1);   // тонны и другие значения с тремя знаками показываем с двумя, остальное с одним
    case "int": case "number": return fmtNum(v, 0);
    case "date": return fmtDate(v);
    case "datetime": return fmtDateTime(v);
    case "bool": return v ? "да" : <span className="muted">нет</span>;
    case "enum": return f.options?.find((o) => o.value === String(v))?.label ?? v;
    case "ref": return r[f.name + "_label"] || <span className="muted">—</span>;
    case "badge": return <Badge tone={(v.tone as Tone) ?? "neutral"}>{v.text}</Badge>;
    case "textarea": return <span title={v}>{String(v).length > 60 ? String(v).slice(0, 60) + "…" : v}</span>;
    default: return v;
  }
}

/** Дополнительные кнопки в строке (печать, «составить документ» и т. п.) для отдельных разделов. */
export type RowActions = (r: Row, done: () => void) => { node: ReactNode; width: number } | null;

/** createExtra: ещё кнопки рядом с «Добавить» (например, «Загрузить из Excel»), видны тем, кто может добавлять. */
export function Registry({ resource, sub, icon, emptyHint, actions, createExtra }: { resource: string; sub?: string; icon?: LucideIcon; emptyHint?: string; actions?: RowActions; createExtra?: ReactNode }) {
  const entity = useEntityParam();
  const { me } = useAuth();
  const [sp, setSp] = useSearchParams();
  const meta = useQuery({ queryKey: ["reg-meta", resource], queryFn: () => get<Meta>(`/${resource}/meta`), staleTime: 60_000 });
  const m = meta.data;

  const read = (k: string) => sp.get(k) ?? "";
  const change = (patchObj: Record<string, string>) =>
    setSp((old) => {
      const next = new URLSearchParams(old);
      for (const [k, v] of Object.entries(patchObj)) v ? next.set(k, v) : next.delete(k);
      if (!("page" in patchObj)) next.delete("page");
      return next;
    }, { replace: true });

  const [text, setText] = useState(read("q"));
  const debounced = useDebounced(text);
  useEffect(() => { if (debounced !== read("q")) change({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps

  // отборы по полям-спискам берутся из описания раздела
  const filterFields = useMemo(() => (m?.fields ?? []).filter((f) => f.filter && f.options), [m]);
  const params: Record<string, string | number | boolean | undefined> = {
    q: read("q"), date_from: read("date_from"), date_to: read("date_to"), state: read("state"), archived: read("archived") === "1",
    legal_entity_id: entity, page: Number(read("page") || 1), page_size: 50,
  };
  for (const f of filterFields) params[f.name] = read(f.name);

  const list = useQuery({ queryKey: ["reg", resource, params], queryFn: () => get<Listing>(`/${resource}`, params), enabled: !!m, placeholderData: (p) => p });
  const [edit, setEdit] = useState<Row | "new" | null>(null);
  const [voiding, setVoiding] = useState<Row | null>(null);
  const [restoring, setRestoring] = useState<Row | null>(null);
  // ссылка вида «?new=1» сразу открывает окно добавления (например, кнопка «Общий расход» в разделе «Расходы»)
  useEffect(() => {
    if (read("new") !== "1" || !m) return;
    if (m.can.create) setEdit("new");
    change({ new: "" });
  }, [m]); // eslint-disable-line react-hooks/exhaustive-deps
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = () => { qc.invalidateQueries({ queryKey: ["reg", resource] }); qc.invalidateQueries({ queryKey: ["options"] }); qc.invalidateQueries({ queryKey: ["reg-meta", resource] }); };

  const multiEntity = m?.scope === "entity" && entity === undefined && (me?.legal_entities.length ?? 0) > 1;
  const columns: Column<Row>[] = m ? [
    ...(multiEntity ? [{ key: "_e", header: "Юрлицо", hideSm: true, render: (r: Row) => r.legal_entity || "—" }] : []),
    ...m.columns.filter((c) => c.first).map((c): Column<Row> => ({ key: c.name, header: c.label, render: (r) => <strong className="nowrap">{cell(c, r)}</strong> })),
    ...m.fields.filter((f) => f.list).map((f, i): Column<Row> => ({
      key: f.name, header: f.kind === "money" || f.kind === "decimal" ? f.label : f.label, num: f.kind === "money" || f.kind === "int" || f.kind === "decimal", hideSm: i > 3,
      render: (r) => (i === 0 && !m.columns.some((c) => c.first) ? <strong>{cell(f, r)}</strong> : cell(f, r)),
    })),
    ...m.columns.filter((c) => !c.first).map((c): Column<Row> => ({ key: c.name, header: c.label, num: c.kind === "money" || c.kind === "number", render: (r) => cell(c, r) })),
    { key: "_a", header: "", width: 48 + (actions ? 40 * (list.data?.items.reduce((n, r) => Math.max(n, actions(r, refresh)?.width ?? 0), 0) ?? 0) : 0), render: (r) => (
      <span onClick={(e) => e.stopPropagation()} className="row-actions">
        {actions?.(r, refresh)?.node}
        {r.archived
          ? r.can_void && <Button variant="ghost" size="sm" icon={ArchiveRestore} aria-label="Вернуть из архива" title="Вернуть из архива" onClick={() => setRestoring(r)} />
          : r.can_void && <Button variant="ghost" size="sm" icon={Archive} aria-label="В архив" title="В архив" onClick={() => setVoiding(r)} />}
      </span>
    ) },
  ] : [];

  const totalsLine = useMemo(() => {
    if (!m || !list.data) return [];
    const items: { label: string; value: number; money: boolean }[] = [];
    for (const f of m.fields) if (f.sum && f.name in list.data.totals) items.push({ label: f.label, value: list.data.totals[f.name], money: f.kind === "money" });
    for (const c of m.columns) if (c.sum && c.name in list.data.totals) items.push({ label: c.label, value: list.data.totals[c.name], money: c.kind === "money" });
    return items;
  }, [m, list.data]);

  const exportParams = { ...params, page: undefined, page_size: undefined };
  const Icon = icon ?? Inbox;

  return (
    <>
      <PageHeader title={m?.title ?? "…"} sub={sub}
        actions={m?.can.create ? <div className="row wrap">{createExtra}<Button variant="primary" icon={Plus} onClick={() => setEdit("new")}>Добавить</Button></div> : undefined} />
      <QueryState q={meta}>
        {m && (
          <Card flush>
            <div className="toolbar" style={{ padding: "14px 14px 0" }}>
              <div style={{ position: "relative", minWidth: 220, flex: "1 1 220px", maxWidth: 340 }}>
                <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
                <input className="input" style={{ paddingLeft: 34 }} placeholder="Поиск" aria-label="Поиск" value={text} onChange={(e) => setText(e.target.value)} />
              </div>
              {m.date_field && (
                <>
                  <input className="input compact" type="date" aria-label="С даты" value={read("date_from")} onChange={(e) => change({ date_from: e.target.value })} />
                  <span className="muted">—</span>
                  <input className="input compact" type="date" aria-label="По дату" value={read("date_to")} onChange={(e) => change({ date_to: e.target.value })} />
                </>
              )}
              {filterFields.map((f) => (
                <select key={f.name} className="input compact" aria-label={f.label} value={read(f.name)} onChange={(e) => change({ [f.name]: e.target.value })}>
                  <option value="">{f.label}: все</option>
                  {f.options!.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              ))}
              <label className="check"><input type="checkbox" checked={read("archived") === "1"} onChange={(e) => change({ archived: e.target.checked ? "1" : "" })} /> Архив</label>
              <span className="spacer" />
              <a className="btn" href={`/api/${resource}/export.xlsx${qs(exportParams)}`} download><Download size={16} aria-hidden /> В Excel</a>
            </div>
            {m.states.length > 0 && (
              <div style={{ padding: "12px 14px 0" }}>
                <FilterChips value={read("state")} onChange={(v) => change({ state: v })} options={[{ id: "", label: "Все" }, ...m.states.map((s) => ({ id: s.value, label: s.label }))]} />
              </div>
            )}
            <div style={{ height: 12 }} />
            <QueryState q={list}>
              {list.data && (list.data.items.length === 0 ? (
                <Empty icon={Icon} title={read("q") || read("state") || read("date_from") || read("date_to") ? "Ничего не найдено" : read("archived") === "1" ? "В архиве пусто" : "Пока пусто"}>
                  {!read("q") && !read("state") && read("archived") !== "1" ? (m.can.create ? emptyHint ?? "Нажмите «Добавить», чтобы создать первую запись." : emptyHint) : undefined}
                </Empty>
              ) : (
                <>
                  {totalsLine.length > 0 && (
                    <div className="row wrap" style={{ padding: "0 14px 12px", gap: 18 }}>
                      {totalsLine.map((t) => <span key={t.label} className="muted">{t.money ? t.label.replace(/, ₽$/, "") : t.label}: <strong style={{ color: "var(--text)" }}>{t.money ? `${fmtMoney(t.value)} ₽` : fmtNum(t.value, 2)}</strong></span>)}
                      <span className="muted small">по всем записям списка</span>
                    </div>
                  )}
                  <Table rows={list.data.items} rowKey={(r) => r.id} dim={(r) => r.archived} columns={columns} onRowClick={(r) => setEdit(r)} />
                  <Pagination page={list.data.page} pageSize={list.data.page_size} total={list.data.total} onPage={(p) => change({ page: String(p) })} />
                </>
              ))}
            </QueryState>
          </Card>
        )}
      </QueryState>
      {m && edit && <RecordModal meta={m} row={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={(warnings) => { refresh(); toast.success("Сохранено"); warnings.forEach((w) => toast.error(w)); }} />}
      {voiding && m && (
        <ConfirmModal title="Убрать в архив" danger confirmLabel="В архив" reasonLabel="Причина"
          text={<>Запись не удаляется: её можно найти в архиве и вернуть. Причина сохранится в журнале.</>}
          onConfirm={async (reason) => { await post(`/${resource}/${voiding.id}/void`, { reason }); refresh(); toast.success("Запись в архиве"); }} onClose={() => setVoiding(null)} />
      )}
      {restoring && m && (
        <ConfirmModal title="Вернуть из архива" confirmLabel="Вернуть"
          onConfirm={async () => { await post(`/${resource}/${restoring.id}/restore`); refresh(); toast.success("Запись возвращена"); }} onClose={() => setRestoring(null)} />
      )}
    </>
  );
}

/* ---------------- карточка записи ---------------- */

type Values = Record<string, any>;

/** ISO со временем → значение поля «дата и время» (по часам браузера, как и везде на сайте). */
function toLocalInput(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

const GLOBAL_REFS = new Set(["counterparty", "customer", "supplier", "tariff", "pay_tariff", "ppe"]);

function initialValues(meta: Meta, row: Row | null, entityId: number | null, defaults?: Record<string, string>): Values {
  const v: Values = {};
  for (const f of meta.fields) {
    if (row) {
      const raw = row[f.name];
      v[f.name] = f.kind === "bool" ? !!raw : raw === null || raw === undefined ? "" : f.kind === "money" || f.kind === "decimal" ? String(raw).replace(".", ",") : f.kind === "datetime" ? toLocalInput(String(raw)) : String(raw);
    } else {
      const d = f.default;
      v[f.name] = f.kind === "bool" ? !!d : d !== null && d !== undefined ? String(d).replace(".", ",") : f.kind === "date" && f.required ? todayIso() : "";
    }
  }
  if (!row && defaults) Object.assign(v, defaults);
  if (!row && meta.scope === "entity" && !meta.entity_from) {
    v.legal_entity_id = entityId && meta.entities.some((e) => e.id === entityId) ? String(entityId) : meta.entities.length === 1 ? String(meta.entities[0].id) : "";
  }
  return v;
}

function RefSelect({ f, value, onChange, entityId, row, error, disabled }: { f: FieldMeta; value: string; onChange: (v: string) => void; entityId?: number | null; row: Row | null; error?: string; disabled?: boolean }) {
  const q = useQuery({ queryKey: ["options", f.ref, entityId ?? null], queryFn: () => get<{ items: { id: number; label: string; legal_entity?: string; group?: string }[] }>(`/options/${f.ref}`, { legal_entity_id: GLOBAL_REFS.has(f.ref ?? "") ? undefined : entityId ?? undefined }), staleTime: 30_000 });
  const items = q.data?.items ?? [];
  const known = items.some((i) => String(i.id) === value);
  const multi = new Set(items.map((i) => i.legal_entity)).size > 1;
  const groups = f.ref === "pay_tariff" ? [...new Set(items.map((i) => i.group ?? ""))] : null;       // тарифы оплаты показываем по группам
  const option = (i: (typeof items)[number]) => <option key={i.id} value={i.id}>{i.label}{multi && i.legal_entity ? ` · ${i.legal_entity}` : ""}</option>;
  return (
    <select className="input" value={value} disabled={disabled} aria-invalid={!!error} onChange={(e) => onChange(e.target.value)}>
      <option value="">{q.isLoading ? "Загрузка…" : f.ref === "pay_tariff" ? "Из карточки сотрудника" : f.required ? "Выберите…" : "—"}</option>
      {value && !known && !q.isLoading && <option value={value}>{row?.[f.name + "_label"] || "Выбрано ранее"}</option>}
      {groups ? groups.map((g) => <optgroup key={g} label={g}>{items.filter((i) => (i.group ?? "") === g).map(option)}</optgroup>) : items.map(option)}
    </select>
  );
}

export function RecordModal({ meta, row, onClose, onSaved, defaults, hide }: { meta: Meta; row: Row | null; onClose: () => void; onSaved: (warnings: string[]) => void; defaults?: Record<string, string>; hide?: string[] }) {
  const { entityId } = useAuth();
  const editing = !!row;
  const readOnly = editing && !row!.can_edit;
  const form = useForm<Values>(initialValues(meta, row, entityId, defaults));
  const { values, set, errors, formError, fail, clear } = form;
  const needsEntity = meta.scope === "entity" && !meta.entity_from;
  const optEntity = meta.scope !== "entity" ? null : editing ? row!.legal_entity_id ?? null : needsEntity ? (values.legal_entity_id ? Number(values.legal_entity_id) : null) : entityId;

  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [pics, setPics] = useState<File[]>([]);
  const photos = !editing ? meta.photos : null;
  const MAX_PICS = 3;

  const save = useMutation({
    mutationFn: async () => {
      clear();
      const body: Values = {};
      for (const f of meta.fields) {
        if (editing && f.locked) continue;
        if (f.show_if && String(values[f.show_if[0]]) !== f.show_if[1]) continue;
        const raw = values[f.name];
        body[f.name] = f.kind === "datetime" && raw ? new Date(raw).toISOString() : raw;
      }
      if (!editing && needsEntity) body.legal_entity_id = values.legal_entity_id;
      if (editing) return patch(`/${meta.key}/${row!.id}`, body);
      const res = await post<{ id: number; warnings?: string[] }>(`/${meta.key}`, body);
      if (photos && pics.length) {
        // запись уже сохранена: если фото не ушло, это не должно выглядеть как «ничего не сохранилось»
        try {
          const form = new FormData();
          (await shrinkAll(pics)).forEach((x) => form.append("files", x));
          await upload(`/entries/${photos.kind}/${res.id}/photos`, form);
        } catch {
          toast.error("Запись сохранена, но фото не загрузилось. Добавьте его кнопкой с камерой в строке записи");
        }
      }
      return res;
    },
    onSuccess: (res: { warnings?: string[] }) => { onSaved(res.warnings ?? []); onClose(); },
    onError: fail,
  });

  const visible = (f: FieldMeta) => !hide?.includes(f.name) && (!f.show_if || String(values[f.show_if[0]]) === f.show_if[1]);
  const renderField = (f: FieldMeta) => {
    const disabled = readOnly || !!row?.archived || (editing && f.locked);
    const err = errors[f.name];
    const label = f.required ? <>{f.label} <span style={{ color: "var(--error)" }} aria-hidden>*</span></> : f.label;
    const hint = editing && f.locked ? "После добавления не меняется" : f.hint;
    let input;
    if (f.kind === "ref") input = <RefSelect f={f} value={values[f.name]} onChange={(v) => set(f.name, v)} entityId={optEntity} row={row} error={err} disabled={disabled} />;
    else if (f.kind === "enum") input = (
      <select className="input" value={values[f.name]} disabled={disabled} aria-invalid={!!err} onChange={(e) => set(f.name, e.target.value)}>
        {!f.required && <option value="">—</option>}
        {f.required && !values[f.name] && <option value="">Выберите…</option>}
        {f.options!.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    );
    else if (f.kind === "bool") input = <span className="check"><input type="checkbox" checked={!!values[f.name]} disabled={disabled} onChange={(e) => set(f.name, e.target.checked)} /> Да</span>;
    else if (f.kind === "textarea") input = <textarea className="input" value={values[f.name]} disabled={disabled} aria-invalid={!!err} maxLength={2000} onChange={(e) => set(f.name, e.target.value)} />;
    else if (f.kind === "datetime") input = <input className="input" type="datetime-local" value={values[f.name]} disabled={disabled} aria-invalid={!!err} onChange={(e) => set(f.name, e.target.value)} />;
    else if (f.kind === "date") input = <input className="input" type="date" value={values[f.name]} disabled={disabled} aria-invalid={!!err} onChange={(e) => set(f.name, e.target.value)} />;
    else {
      const list = f.suggest && meta.suggestions[f.name]?.length ? `sg-${meta.key}-${f.name}` : undefined;
      input = (
        <>
          <input className="input" value={values[f.name]} disabled={disabled} aria-invalid={!!err} list={list} maxLength={f.max}
            inputMode={f.kind === "money" || f.kind === "decimal" ? "decimal" : f.kind === "int" ? "numeric" : undefined} onChange={(e) => set(f.name, e.target.value)} />
          {list && <datalist id={list}>{meta.suggestions[f.name].map((s) => <option key={s} value={s} />)}</datalist>}
        </>
      );
    }
    return <Field key={f.name} label={label} error={err} hint={hint} full={f.wide}>{input}</Field>;
  };

  /** Дни недели в расписании маршрутов рисуются одной строкой кнопок «Пн Вт Ср…», а не семью отдельными полями. */
  const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const WEEK_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  const renderDays = () => {
    const disabled = readOnly || !!row?.archived;
    const err = errors.mon;
    return (
      <div className="field full" key="week-days">
        <span className="label" id="week-days-label">Дни недели <span style={{ color: "var(--error)" }} aria-hidden>*</span></span>
        <div className="day-chips" role="group" aria-labelledby="week-days-label">
          {WEEK.map((k, i) => (
            <label key={k} className={`day-chip ${values[k] ? "on" : ""}`}>
              <input type="checkbox" checked={!!values[k]} disabled={disabled} onChange={(e) => { set(k, e.target.checked); if (errors.mon) set("mon", k === "mon" ? e.target.checked : !!values.mon); }} />
              <span>{WEEK_SHORT[i]}</span>
            </label>
          ))}
        </div>
        {err && <span className="err" role="alert">{err}</span>}
      </div>
    );
  };
  const renderFields = (fs: FieldMeta[]) => {
    const out: ReactNode[] = [];
    let days = false;
    for (const f of fs) {
      if (meta.key === "route_plan" && WEEK.includes(f.name)) {
        if (!days) { days = true; out.push(renderDays()); }
        continue;
      }
      out.push(renderField(f));
    }
    return out;
  };

  const moreFields = meta.fields.filter((f) => visible(f) && f.more);
  const title = readOnly ? meta.title : editing ? "Изменить" : "Добавить";
  return (
    <Modal title={`${title}: ${meta.one}`} wide onClose={onClose}
      footer={<>
        <Button onClick={onClose}>{readOnly ? "Закрыть" : "Отмена"}</Button>
        {!readOnly && <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Сохранить</Button>}
      </>}>
      <form onSubmit={(e) => { e.preventDefault(); if (!readOnly) save.mutate(); }} className="stack-sm" noValidate>
        {formError && !Object.keys(errors).length && <Alert tone="error">{formError}</Alert>}
        {editing && row!.archived && <Alert tone="warning">Запись в архиве{row!.archive_reason ? `: ${row!.archive_reason}` : ""}. Чтобы её править, верните её из архива.</Alert>}
        <div className="form-grid">
          {needsEntity && (
            <Field label="Юридическое лицо" error={errors.legal_entity_id} full={meta.entities.length <= 1}>
              {editing ? <input className="input" value={row!.legal_entity ?? ""} disabled /> : (
                <select className="input" value={values.legal_entity_id} aria-invalid={!!errors.legal_entity_id} onChange={(e) => set("legal_entity_id", e.target.value)}>
                  <option value="">Выберите…</option>
                  {meta.entities.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                </select>
              )}
            </Field>
          )}
          {renderFields(meta.fields.filter((f) => visible(f) && !f.more))}
        </div>
        {meta.key === "worklog" && !readOnly && !row?.archived && <WorklogPreview values={values} editingId={row?.id} />}
        {photos && (
          <div className="field full">
            <span className="label">{photos.label}</span>
            <input ref={fileInput} type="file" accept="image/*" multiple hidden
              onChange={(ev) => { const files = Array.from(ev.target.files ?? []); ev.target.value = ""; setPics((cur) => [...cur, ...files].slice(0, MAX_PICS)); }} />
            <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
              <Button icon={Camera} disabled={pics.length >= MAX_PICS} onClick={() => fileInput.current?.click()}>{pics.length ? "Добавить ещё" : "Прикрепить фото"}</Button>
              {pics.map((f, i) => (
                <span className="chip" key={`${f.name}-${i}`}>
                  {f.name.length > 22 ? `${f.name.slice(0, 20)}…` : f.name}
                  <button type="button" className="chip-x" aria-label={`Убрать фото ${f.name}`} onClick={() => setPics((cur) => cur.filter((_, j) => j !== i))}><X size={12} aria-hidden /></button>
                </span>
              ))}
            </div>
            <span className="muted small">Не больше {MAX_PICS} фото. Можно сфотографировать прямо с телефона.</span>
          </div>
        )}
        {moreFields.length > 0 && (
          <details className="more-fields" open={editing}>
            <summary>Дополнительно (можно не заполнять)</summary>
            <div className="form-grid" style={{ marginTop: 12 }}>{moreFields.map(renderField)}</div>
          </details>
        )}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
