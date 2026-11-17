// Новый счёт на ремонт (выписать в программе или внести счёт поставщика) и правка счёта.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileSearch, Paperclip, X } from "lucide-react";
import { api, ApiError, get, patch } from "../api";
import { fmtMoney, fmtSize, todayIso } from "../format";
import { useAuth } from "../auth";
import { useVehicleOptions } from "../components/pickers";
import { BILL_UNITS, emptyPart, filledParts, PartsEditor, type PartRow } from "../components/PartsEditor";
import { Alert, Button, Card, Field, NotFound, PageHeader, QueryState, Spinner, useForm, useToast } from "../ui";
import { clearHandoff, peekHandoff } from "./billAuto";
import { useBillMeta, type BillMeta } from "./Bills";

interface Detail {
  id: number; legal_entity_id: number; kind: string; number: string; bill_date: string; supplier_id: number | null;
  supplier_name: string; supplier_inn: string; supplier_kpp: string; supplier_addr: string; supplier_phone: string; bank_name: string; bik: string; corr_account: string; account: string;
  basis: string; vehicle_id: number | null; request_id: number | null; request: { id: number | null; number: number } | null; vat_pct: number; total: number; vat: number; note: string;
  items: { name: string; article: string; qty_text: string; unit: string; price: number }[]; can: { edit: boolean };
}
interface Prefill {
  legal_entity_id: number; vehicle_id: number; vehicle: string; request_id: number; request_number: number; request_title: string; basis: string;
  items: { name: string; article: string; qty: string; unit: string; price: string }[];
}
interface Found { [k: string]: any }

const num = (s: string) => { const n = Number(String(s ?? "").trim().replace(/\s/g, "").replace(",", ".")); return Number.isFinite(n) ? n : NaN; };
const r2 = (x: number) => Math.round((x + Number.EPSILON) * 100) / 100;

/** Примерный итог по строкам для подсказки в форме. Окончательно считает сервер. */
function preview(rows: PartRow[], pct: number) {
  let total = 0, vat = 0;
  for (const r of filledParts(rows)) {
    const q = num(r.qty), p = num(r.price ?? "");
    if (!Number.isFinite(q) || !Number.isFinite(p)) continue;
    const a = r2(q * p);
    total += a;
    vat += r2((a * pct) / (100 + pct));
  }
  return { total: r2(total), vat: r2(vat) };
}

function Body({ edit, meta, prefill, kind: kind0 }: { edit?: Detail; meta: BillMeta; prefill?: Prefill; kind: "issued" | "uploaded" }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { can, entityId } = useAuth();
  const kind = edit ? (edit.kind as "issued" | "uploaded") : kind0;
  const issued = kind === "issued";
  // Если человек пришёл из окна «Загрузить счёт», здесь уже есть прочитанное и приложенные файлы
  const [handoff] = useState(() => (!edit && !prefill && kind === "uploaded" ? peekHandoff() : null));
  const hf = handoff?.found ?? {};
  const f = useForm({
    number: edit?.number ?? hf.number ?? "", bill_date: edit?.bill_date ?? hf.bill_date ?? todayIso(), supplier_id: edit?.supplier_id ? String(edit.supplier_id) : hf.supplier_id ? String(hf.supplier_id) : "",
    supplier_name: edit?.supplier_name ?? hf.supplier_name ?? "", supplier_inn: edit?.supplier_inn ?? hf.supplier_inn ?? "", supplier_kpp: edit?.supplier_kpp ?? hf.supplier_kpp ?? "", supplier_addr: edit?.supplier_addr ?? hf.supplier_addr ?? "",
    supplier_phone: edit?.supplier_phone ?? hf.supplier_phone ?? "", bank_name: edit?.bank_name ?? hf.bank_name ?? "", bik: edit?.bik ?? hf.bik ?? "", corr_account: edit?.corr_account ?? hf.corr_account ?? "", account: edit?.account ?? hf.account ?? "",
    basis: edit?.basis ?? prefill?.basis ?? hf.basis ?? "", vehicle_id: String(edit?.vehicle_id ?? prefill?.vehicle_id ?? hf.vehicle_id ?? ""), request_id: String(edit?.request_id ?? prefill?.request_id ?? ""),
    legal_entity_id: String(edit?.legal_entity_id ?? prefill?.legal_entity_id ?? hf.legal_entity_id ?? (handoff?.legalEntityId || (meta.entities.length === 1 ? meta.entities[0].id : meta.entities.some((x) => x.id === entityId) ? entityId : ""))),
    vat_pct: String(edit?.vat_pct ?? hf.vat_pct ?? 22), total: edit ? String(edit.total).replace(".", ",") : hf.total != null ? String(hf.total).replace(".", ",") : "", vat: !edit && hf.vat != null ? String(hf.vat).replace(".", ",") : "",
    paid: false, paid_date: "", note: edit?.note ?? handoff?.note ?? "",
  });
  const [rows, setRows] = useState<PartRow[]>(() =>
    edit?.items.length ? edit.items.map((i) => ({ name: i.name, article: i.article, qty: i.qty_text, unit: i.unit, price: String(i.price).replace(".", ",") }))
      : prefill?.items.length ? prefill.items.map((i) => ({ ...i })) : [emptyPart(true)]);
  const [files, setFiles] = useState<File[]>(handoff?.files ?? []);
  const [recNote, setRecNote] = useState<{ tone: "info" | "warning"; text: string } | null>(handoff ? { tone: "warning", text: handoff.message } : null);
  const [showBank, setShowBank] = useState(!!(edit && (edit.bank_name || edit.bik || edit.account)) || issued || !!(handoff && (hf.bank_name || hf.bik || hf.account || hf.supplier_addr || hf.supplier_phone)));
  const fileRef = useRef<HTMLInputElement>(null);
  const vehicles = useVehicleOptions(f.values.legal_entity_id ? Number(f.values.legal_entity_id) : undefined);
  const suppliers = useQuery({ queryKey: ["opt-supplier"], queryFn: () => get<{ items: { id: number; label: string }[] }>("/options/supplier"), retry: false, staleTime: 60_000 });
  const reqs = useQuery({
    queryKey: ["bill-requests", f.values.vehicle_id], enabled: !!f.values.vehicle_id && can("requests.view"), retry: false,
    queryFn: () => get<{ items: { id: number; number: number; title: string; status_label: string }[] }>("/requests", { vehicle_id: f.values.vehicle_id, page_size: 100 }),
  });
  const pct = Number(f.values.vat_pct);
  const calc = useMemo(() => preview(rows, pct), [rows, pct]);
  const lockedEntity = !!(edit || prefill || f.values.vehicle_id);

  // Подсветить то, что программа не прочитала или в чём не уверена; пришли мы из окна «Загрузить счёт»
  useEffect(() => {
    if (!handoff) return;
    clearHandoff();
    const errs: Record<string, string> = { ...handoff.errors };
    for (const k of handoff.missing) errs[k] ||= k === "legal_entity_id" ? "Выберите юрлицо" : "Впишите: программа не смогла прочитать";
    if (Object.keys(errs).length) f.fail(new ApiError(422, "auto_read", handoff.message, null, errs));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const recognize = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.append("file", file); return api<{ found: Found; readable: boolean; note: string }>("POST", "/bills/recognize", fd); },
    onSuccess: (r) => {
      const found = r.found ?? {};
      const keys = Object.keys(found).filter((k) => found[k] !== null && found[k] !== "" && k !== "supplier_id" && k !== "vehicle_id");
      for (const k of keys) f.set(k as any, k === "total" || k === "vat" ? String(found[k]).replace(".", ",") : String(found[k]));
      if (found.vat_pct !== undefined && found.vat_pct !== null) f.set("vat_pct", String(found.vat_pct));
      if (found.supplier_id) f.set("supplier_id", String(found.supplier_id));
      if (found.vehicle_id && !f.values.vehicle_id) f.set("vehicle_id", String(found.vehicle_id));
      setShowBank(true);
      setRecNote({ tone: keys.length ? "info" : "warning", text: r.note });
    },
    onError: (e: any) => setRecNote({ tone: "warning", text: e.message }),
  });

  const m = useMutation({
    mutationFn: async () => {
      const v = f.values;
      const body: Record<string, unknown> = {
        number: v.number, bill_date: v.bill_date, supplier_name: v.supplier_name, supplier_inn: v.supplier_inn, supplier_kpp: v.supplier_kpp, supplier_addr: v.supplier_addr,
        supplier_phone: v.supplier_phone, bank_name: v.bank_name, bik: v.bik, corr_account: v.corr_account, account: v.account, basis: v.basis, note: v.note, vat_pct: pct,
      };
      if (v.supplier_id) body.supplier_id = Number(v.supplier_id);
      if (v.vehicle_id) body.vehicle_id = Number(v.vehicle_id); else if (edit?.vehicle_id) body.vehicle_id = null;
      if (v.request_id) body.request_id = Number(v.request_id); else if (edit?.request_id) body.request_id = null;
      if (!edit && v.legal_entity_id) body.legal_entity_id = Number(v.legal_entity_id);
      if (issued) {
        const items = filledParts(rows).map(({ name, article, qty, unit, price }) => ({ name, article, qty, unit, price }));
        if (!items.length) throw new ApiError(422, "no_items", "Добавьте хотя бы одну строку", "items");
        body.items = items;
      } else {
        body.total = v.total;
        if (v.vat.trim()) body.vat = v.vat;
      }
      if (!edit && v.paid && can("bills.pay")) { body.status = "paid"; if (v.paid_date) body.paid_date = v.paid_date; }
      if (edit) return patch<{ id: number }>(`/bills/${edit.id}`, body);
      body.kind = kind;
      const fd = new FormData();
      fd.append("data", JSON.stringify(body));
      files.forEach((x) => fd.append("files", x));
      return api<{ id: number }>("POST", "/bills", fd);
    },
    onSuccess: (r) => { qc.invalidateQueries(); toast.success(edit ? "Счёт сохранён" : "Счёт внесён"); nav(`/bills/${r.id}`); },
    onError: f.fail,
  });

  const pick = (list: FileList | null) => {
    if (!list) return;
    const add = [...list];
    setFiles((s) => [...s, ...add].slice(0, 10));
    const readable = add.find((x) => x.type === "application/pdf" || x.type.startsWith("image/") || /\.(pdf|jpe?g|png|webp)$/i.test(x.name));
    if (readable && !edit && !f.values.number && !f.values.total) recognize.mutate(readable);
    if (fileRef.current) fileRef.current.value = "";
  };
  const e = f.errors;
  const itemsErr = e.items ?? e.name ?? e.qty ?? e.unit;
  const otherErrors = Object.keys(e).filter((k) => !["number", "bill_date", "supplier_name", "supplier_inn", "supplier_kpp", "supplier_addr", "supplier_phone", "bank_name", "bik", "corr_account", "account", "basis", "vehicle_id", "request_id", "legal_entity_id", "vat_pct", "total", "vat", "items", "name", "qty", "unit", "paid_date", "status", "note", "files"].includes(k));

  return (
    <>
      <PageHeader crumb={<Link to={edit ? `/bills/${edit.id}` : "/bills"}><ArrowLeft size={13} aria-hidden /> {edit ? `Счёт № ${edit.number}` : "Счета на ремонт"}</Link>}
        title={edit ? `Изменить счёт № ${edit.number}` : issued ? "Выписать счёт" : "Внести счёт поставщика"}
        sub={issued ? "Счёт составляется здесь и печатается на бланке. Цены вводятся с НДС." : "Счёт уже получен от поставщика: впишите данные и приложите файл или фото."} />
      <form onSubmit={(ev) => { ev.preventDefault(); f.clear(); m.mutate(); }} noValidate>
        <div className="stack">
          {f.formError && (otherErrors.length > 0 || !Object.keys(e).length) && <Alert tone="error">{f.formError}</Alert>}
          {!issued && !edit && (
            <Card title="Файл счёта">
              <div className="stack-sm">
                <div className="row wrap">
                  <Button icon={Paperclip} onClick={() => fileRef.current?.click()}>Приложить файл или фото</Button>
                  <input ref={fileRef} type="file" hidden multiple accept="application/pdf,image/jpeg,image/png,image/webp" onChange={(ev) => pick(ev.target.files)} />
                  <span className="muted small">PDF, JPEG, PNG или WebP, до 10 МБ каждый, не больше 10 файлов</span>
                </div>
                {files.length > 0 && (
                  <ul className="file-list">
                    {files.map((x, i) => (
                      <li key={i}><Paperclip size={14} aria-hidden /> <span className="grow">{x.name} <span className="muted small">{fmtSize(x.size)}</span></span>
                        <Button size="sm" icon={FileSearch} loading={recognize.isPending} onClick={() => recognize.mutate(x)}>Прочитать данные</Button>
                        <Button variant="ghost" size="sm" icon={X} aria-label={`Убрать файл ${x.name}`} onClick={() => setFiles((s) => s.filter((_, k) => k !== i))} />
                      </li>
                    ))}
                  </ul>
                )}
                {e.files && <span className="err" role="alert">{e.files}</span>}
                {recNote && <Alert tone={recNote.tone}>{recNote.text}</Alert>}
                <div className="muted small">Данные из PDF, скана и фото программа читает сама. С фото и сканов бывают ошибки в цифрах: проверьте номер, дату и сумму.</div>
              </div>
            </Card>
          )}
          <Card title="Счёт">
            <div className="form-grid">
              {!lockedEntity && meta.entities.length > 1 && (
                <Field label="Юридическое лицо (обязательно)" error={e.legal_entity_id} hint="Кто платит по счёту">
                  <select className="input" value={f.values.legal_entity_id} onChange={(ev) => f.set("legal_entity_id", ev.target.value)}>
                    <option value="">Выберите…</option>{meta.entities.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                  </select>
                </Field>
              )}
              <Field label={issued ? "Номер счёта" : "Номер счёта поставщика"} error={e.number} hint={issued && !edit ? "Можно оставить пустым: номер присвоится сам, по порядку в году" : undefined}>
                <input className="input" value={f.values.number} maxLength={40} onChange={(ev) => f.set("number", ev.target.value)} />
              </Field>
              <Field label="Дата счёта" error={e.bill_date}><input className="input" type="date" value={f.values.bill_date} onChange={(ev) => f.set("bill_date", ev.target.value)} /></Field>
              <Field label="Машина" error={e.vehicle_id} hint="Если счёт относится к конкретной машине">
                <select className="input" value={f.values.vehicle_id} disabled={!!prefill?.request_id && !edit} onChange={(ev) => { f.set("vehicle_id", ev.target.value); f.set("request_id", ""); }}>
                  <option value="">Не привязан к машине</option>
                  {vehicles.data?.items.map((v) => <option key={v.id} value={v.id}>{v.plate} — {v.name || "без названия"}</option>)}
                </select>
              </Field>
              {f.values.vehicle_id && (reqs.data?.items.length || f.values.request_id) ? (
                <Field label="Обращение" error={e.request_id} hint="Сумма попадёт в расходы по этому обращению">
                  <select className="input" value={f.values.request_id} disabled={!!prefill?.request_id && !edit} onChange={(ev) => f.set("request_id", ev.target.value)}>
                    <option value="">Не привязан к обращению</option>
                    {f.values.request_id && !reqs.data?.items.some((x) => String(x.id) === f.values.request_id) && <option value={f.values.request_id}>№ {prefill?.request_number ?? edit?.request?.number}</option>}
                    {reqs.data?.items.map((x) => <option key={x.id} value={x.id}>№ {x.number} — {x.title} ({x.status_label})</option>)}
                  </select>
                </Field>
              ) : null}
              <Field label="Основание" error={e.basis} full><input className="input" value={f.values.basis} maxLength={120} placeholder="Например: Обращение № 15, замена масла" onChange={(ev) => f.set("basis", ev.target.value)} /></Field>
            </div>
          </Card>
          <Card title="Поставщик">
            <div className="form-grid">
              {suppliers.data && suppliers.data.items.length > 0 && (
                <Field label="Из справочника" full error={e.supplier_id} hint="Реквизиты подставятся из карточки контрагента в пустые поля ниже">
                  <select className="input" value={f.values.supplier_id} onChange={(ev) => f.set("supplier_id", ev.target.value)}>
                    <option value="">Не выбран: впишу вручную</option>{suppliers.data.items.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
                  </select>
                </Field>
              )}
              <Field label="Название поставщика" error={e.supplier_name} full><input className="input" value={f.values.supplier_name} maxLength={160} onChange={(ev) => f.set("supplier_name", ev.target.value)} /></Field>
              <Field label="ИНН" error={e.supplier_inn}><input className="input" inputMode="numeric" value={f.values.supplier_inn} maxLength={12} onChange={(ev) => f.set("supplier_inn", ev.target.value)} /></Field>
              <Field label="КПП" error={e.supplier_kpp}><input className="input" inputMode="numeric" value={f.values.supplier_kpp} maxLength={9} onChange={(ev) => f.set("supplier_kpp", ev.target.value)} /></Field>
              {!showBank && <div className="field full"><Button size="sm" onClick={() => setShowBank(true)}>Адрес, телефон и банковские реквизиты</Button></div>}
              {showBank && <>
                <Field label="Адрес" error={e.supplier_addr} full><input className="input" value={f.values.supplier_addr} maxLength={250} onChange={(ev) => f.set("supplier_addr", ev.target.value)} /></Field>
                <Field label="Телефон" error={e.supplier_phone}><input className="input" value={f.values.supplier_phone} maxLength={40} onChange={(ev) => f.set("supplier_phone", ev.target.value)} /></Field>
                <Field label="Банк" error={e.bank_name}><input className="input" value={f.values.bank_name} maxLength={160} onChange={(ev) => f.set("bank_name", ev.target.value)} /></Field>
                <Field label="БИК" error={e.bik}><input className="input" inputMode="numeric" value={f.values.bik} maxLength={9} onChange={(ev) => f.set("bik", ev.target.value)} /></Field>
                <Field label="Корр. счёт" error={e.corr_account}><input className="input" inputMode="numeric" value={f.values.corr_account} maxLength={20} onChange={(ev) => f.set("corr_account", ev.target.value)} /></Field>
                <Field label="Расчётный счёт" error={e.account} hint={issued ? "Нужен для печатной формы счёта" : undefined}><input className="input" inputMode="numeric" value={f.values.account} maxLength={20} onChange={(ev) => f.set("account", ev.target.value)} /></Field>
              </>}
            </div>
          </Card>
          <Card title={issued ? "Что в счёте" : "Сумма"}>
            {issued ? (
              <div className="stack-sm">
                <PartsEditor rows={rows} onChange={setRows} units={BILL_UNITS} withPrice max={100} errors={itemsErr} hint="Цена за единицу с НДС. Количество может быть дробным, например 4,5 л." />
                <div className="row wrap" style={{ gap: 18 }}>
                  <Field label="НДС" error={e.vat_pct}>
                    <select className="input compact" value={f.values.vat_pct} onChange={(ev) => f.set("vat_pct", ev.target.value)}>{meta.vat_rates.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}</select>
                  </Field>
                  <div className="calc-line"><span className="muted small">Итого (предварительно)</span><strong>{fmtMoney(calc.total)} ₽</strong><span className="muted small">в т.ч. НДС {fmtMoney(calc.vat)} ₽</span></div>
                </div>
              </div>
            ) : (
              <div className="form-grid">
                <Field label="Сумма счёта, ₽" error={e.total}><input className="input" inputMode="decimal" value={f.values.total} placeholder="90804,92" onChange={(ev) => f.set("total", ev.target.value)} /></Field>
                <Field label="Ставка НДС" error={e.vat_pct}><select className="input" value={f.values.vat_pct} onChange={(ev) => f.set("vat_pct", ev.target.value)}>{meta.vat_rates.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}</select></Field>
                <Field label="НДС в счёте, ₽" error={e.vat} hint="Если не вписать, посчитаем по ставке"><input className="input" inputMode="decimal" value={f.values.vat} onChange={(ev) => f.set("vat", ev.target.value)} /></Field>
              </div>
            )}
          </Card>
          <Card title="Оплата и заметка">
            <div className="form-grid">
              {!edit && meta.can.pay && (
                <>
                  <div className="field"><label className="row" style={{ gap: 8 }}><input type="checkbox" checked={f.values.paid} onChange={(ev) => f.set("paid", ev.target.checked)} /> <span>Счёт уже оплачен</span></label></div>
                  {f.values.paid && <Field label="Дата оплаты" error={e.paid_date ?? e.status} hint="Пусто — сегодня"><input className="input" type="date" value={f.values.paid_date} max={todayIso()} onChange={(ev) => f.set("paid_date", ev.target.value)} /></Field>}
                </>
              )}
              <Field label="Заметка" full error={e.note}><textarea className="input" value={f.values.note} maxLength={500} onChange={(ev) => f.set("note", ev.target.value)} /></Field>
            </div>
          </Card>
          <div className="row wrap" style={{ justifyContent: "flex-end" }}>
            <Button onClick={() => nav(edit ? `/bills/${edit.id}` : "/bills")}>Отмена</Button>
            <Button type="submit" variant="primary" loading={m.isPending}>{edit ? "Сохранить" : issued ? "Выписать счёт" : "Внести счёт"}</Button>
          </div>
        </div>
      </form>
    </>
  );
}

export function BillForm() {
  const { id } = useParams();
  const [sp] = useSearchParams();
  const meta = useBillMeta();
  const bid = id ? Number(id) : null;
  const request = sp.get("request");
  const detail = useQuery({ queryKey: ["bill", bid], enabled: bid !== null && Number.isInteger(bid), queryFn: () => get<Detail>(`/bills/${bid}`) });
  const prefill = useQuery({ queryKey: ["bill-prefill", request], enabled: !!request, retry: false, queryFn: () => get<Prefill>("/bills/prefill", { request: Number(request) }) });
  if (bid !== null && !Number.isInteger(bid)) return <NotFound what="Счёт" />;
  if (meta.isLoading || (bid !== null && detail.isLoading) || (request && prefill.isLoading)) return <Spinner />;
  if (meta.error || !meta.data) return <QueryState q={meta} what="Счёт"><></></QueryState>;
  if (bid !== null) {
    if (detail.error || !detail.data) return <QueryState q={detail} what="Счёт"><></></QueryState>;
    if (!detail.data.can.edit) return <Alert tone="warning">Этот счёт изменить нельзя: он в архиве или у вас нет права на правку.</Alert>;
    return <Body key={detail.data.id} edit={detail.data} meta={meta.data} kind={detail.data.kind as "issued" | "uploaded"} />;
  }
  if (!meta.data.can.create) return <Alert tone="warning">У вас нет права вносить счета.</Alert>;
  const kind = sp.get("kind") === "issued" ? "issued" : "uploaded";
  return (
    <>
      {request && prefill.error && <Alert tone="warning">Обращение не найдено или по нему нельзя вносить счета: форма открыта без заготовки.</Alert>}
      <Body key={`${kind}-${request}`} meta={meta.data} prefill={prefill.data} kind={kind} />
    </>
  );
}
