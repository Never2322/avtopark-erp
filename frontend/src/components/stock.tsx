// Склад: общие части — справочники для форм, окно «Приход / Расход / Списание / Перемещение», приём товара по счёту.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, get, post } from "../api";
import { fmtMoney, todayIso } from "../format";
import { Alert, Badge, Button, Field, FilterChips, Modal, Table, useForm, useToast, type Tone } from "../ui";

export interface StockPlace { warehouse_id: number; qty: number; qty_text: string }
export interface StockOptItem { id: number; name: string; sku: string; unit: string; min_qty: number; legal_entity_id: number; total: number; total_text: string; places: StockPlace[] }
export interface StockOptions {
  warehouses: { id: number; name: string; legal_entity_id: number }[];
  items: StockOptItem[];
  vehicles: { id: number; label: string; legal_entity_id: number }[];
  suppliers: { id: number; label: string }[];
  kinds: { code: string; label: string }[];
  entities: Record<string, string>;
  can: { create: boolean; edit: boolean; void: boolean; items_create: boolean; warehouses_create: boolean; moves_view: boolean; items_view: boolean };
}

export const KIND_TONE: Record<string, Tone> = { receipt: "success", issue: "info", writeoff: "warning", transfer: "neutral" };
const TITLES: Record<string, string> = { receipt: "Приход на склад", issue: "Расход со склада", writeoff: "Списание", transfer: "Перемещение между складами" };

export function useStockOptions(entity?: number | null, enabled = true) {
  return useQuery({ queryKey: ["stock-options", entity ?? null], enabled, staleTime: 15_000,
    queryFn: () => get<StockOptions>("/stock/options", { legal_entity_id: entity ?? undefined }) });
}

interface CatalogPart { id: number; name: string; article: string; unit: string; category: string }
const UNITS = ["шт", "комплект", "л", "кг", "м", "пара"];
type Source = "stock" | "catalog" | "manual";

/** Деталь из каталога: поиск по названию или артикулу, сначала самые частые. */
function CatalogPicker({ value, onPick }: { value: CatalogPart | null; onPick: (c: CatalogPart | null) => void }) {
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["part-options", q], queryFn: () => get<{ items: CatalogPart[] }>("/part-options", { q, limit: 30 }), staleTime: 30_000 });
  if (value) return (
    <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
      <span><strong>{value.name}</strong>{value.article ? <span className="muted"> · {value.article}</span> : null} <span className="muted small">({value.unit})</span></span>
      <Button size="sm" onClick={() => onPick(null)}>Другая деталь</Button>
    </div>
  );
  return (
    <div className="stack-sm">
      <input className="input" placeholder="Найти в каталоге по названию или артикулу" aria-label="Поиск в каталоге" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="catalog-list" role="listbox" aria-label="Детали каталога">
        {list.isLoading && <div className="muted small" style={{ padding: 8 }}>Ищу…</div>}
        {list.data?.items.length === 0 && <div className="muted small" style={{ padding: 8 }}>В каталоге ничего не нашлось. Впишите товар вручную.</div>}
        {list.data?.items.map((c) => (
          <button key={c.id} type="button" role="option" aria-selected={false} aria-label={c.article ? `${c.name}, ${c.article}` : c.name} className="catalog-row" onClick={() => onPick(c)}>
            <span>{c.name}{c.article ? <span className="muted"> · {c.article}</span> : null}</span>
            <span className="muted small">{c.category || c.unit}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

const itemLabel = (i: StockOptItem, many: boolean, ents: Record<string, string>) =>
  `${many ? `[${ents[String(i.legal_entity_id)] ?? ""}] ` : ""}${i.name}${i.sku ? ` · ${i.sku}` : ""} — на складах ${i.total_text} ${i.unit}`;

/** Движение товара: вид выбирается сверху, поля меняются под вид. Остаток после операции показывается в сообщении. */
export function StockMoveModal({ kind: first = "receipt", itemId, entity, onClose }: { kind?: string; itemId?: number; entity?: number | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const opts = useStockOptions(entity);
  const o = opts.data;
  const f = useForm({ kind: first, item_id: itemId ? String(itemId) : "", filter: "", warehouse_id: "", to_warehouse_id: "", qty: "", price: "",
    move_date: todayIso(), counterparty_id: "", vehicle_id: "", doc_number: "", note: "" });
  const v = f.values;
  const [source, setSource] = useState<Source>("stock");
  const [part, setPart] = useState<CatalogPart | null>(null);
  const [manual, setManual] = useState({ name: "", sku: "", unit: "шт" });
  const isNew = v.kind === "receipt" && source !== "stock";
  const many = !!o && Object.keys(o.entities).length > 1;
  const kinds = (o?.kinds ?? []).map((k) => ({ id: k.code, label: k.label }));
  const item = isNew ? undefined : o?.items.find((i) => String(i.id) === v.item_id);
  const whs = useMemo(() => (o?.warehouses ?? []).filter((w) => !item || w.legal_entity_id === item.legal_entity_id), [o, item]);
  const vehicles = useMemo(() => (o?.vehicles ?? []).filter((x) => !item || x.legal_entity_id === item.legal_entity_id), [o, item]);
  const candidates = useMemo(() => {
    const t = v.filter.trim().toLowerCase();
    const list = (o?.items ?? []).filter((i) => (v.kind === "receipt" || i.total > 0 || String(i.id) === v.item_id) && (!t || `${i.name} ${i.sku}`.toLowerCase().includes(t)));
    return list.slice(0, 300);
  }, [o, v.filter, v.kind, v.item_id]);
  const qtyAt = (wid: number) => item?.places.find((p) => p.warehouse_id === wid)?.qty_text ?? "0";

  // склад подставляется сам, если он один; при смене товара выбор склада сбрасывается, если склад чужого юрлица
  useEffect(() => {
    if (!o) return;
    if (v.warehouse_id && !whs.some((w) => String(w.id) === v.warehouse_id)) f.set("warehouse_id", "");
    if (v.to_warehouse_id && !whs.some((w) => String(w.id) === v.to_warehouse_id)) f.set("to_warehouse_id", "");
    if (!v.warehouse_id && whs.length === 1) f.set("warehouse_id", String(whs[0].id));
  }, [o, whs]); // eslint-disable-line react-hooks/exhaustive-deps
  // для расхода склад подсказывается по товару: где его больше всего
  useEffect(() => {
    if (!item || v.kind === "receipt" || v.warehouse_id) return;
    const best = [...item.places].sort((a, b) => b.qty - a.qty)[0];
    if (best && whs.some((w) => w.id === best.warehouse_id)) f.set("warehouse_id", String(best.warehouse_id));
  }, [item?.id, v.kind]); // eslint-disable-line react-hooks/exhaustive-deps

  const m = useMutation({
    mutationFn: () => {
      if (isNew && source === "catalog" && !part) throw new ApiError(422, "no_item", "Выберите деталь из каталога", "item_id");
      if (isNew && source === "manual" && !manual.name.trim()) throw new ApiError(422, "part_name", "Впишите название товара", "new_name");
      if (!isNew && !item) throw new ApiError(422, "no_item", "Выберите товар", "item_id");
      if (!v.warehouse_id) throw new ApiError(422, "no_warehouse", v.kind === "transfer" ? "Выберите склад, откуда перемещаем" : "Выберите склад", "warehouse_id");
      if (v.kind === "transfer" && !v.to_warehouse_id) throw new ApiError(422, "no_warehouse", "Выберите склад, куда перемещаем", "to_warehouse_id");
      return post<{ left_here: string; left_total: string; move: { unit: string; kind_label: string } }>("/stock/moves", {
        kind: v.kind, item_id: isNew ? null : item!.id,
        new_item: !isNew ? null : source === "catalog" ? { catalog_id: part!.id } : { name: manual.name, sku: manual.sku, unit: manual.unit },
        warehouse_id: Number(v.warehouse_id), to_warehouse_id: v.kind === "transfer" ? Number(v.to_warehouse_id) : null,
        qty: v.qty, price: v.kind === "receipt" && v.price.trim() ? v.price : null, move_date: v.move_date || null,
        counterparty_id: v.kind === "receipt" && v.counterparty_id ? Number(v.counterparty_id) : null,
        vehicle_id: (v.kind === "issue" || v.kind === "writeoff") && v.vehicle_id ? Number(v.vehicle_id) : null,
        doc_number: v.doc_number, note: v.note,
      });
    },
    onSuccess: (r) => {
      qc.invalidateQueries();
      const nm = item?.name ?? (source === "catalog" ? part?.name : manual.name.trim()) ?? "";
      const u = item?.unit ?? r.move.unit;
      toast.success(`${r.move.kind_label}: готово. Остаток «${nm}»: на этом складе ${r.left_here} ${u}, всего ${r.left_total} ${u}`);
      onClose();
    },
    onError: f.fail,
  });

  const noWarehouse = !!o && o.warehouses.length === 0;
  const unit = item?.unit ?? (isNew ? (source === "catalog" ? part?.unit ?? "" : manual.unit) : "");
  return (
    <Modal title={TITLES[v.kind] ?? "Движение товара"} wide onClose={onClose}
      footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={!o || noWarehouse} onClick={() => { f.clear(); m.mutate(); }}>Записать</Button></>}>
      <div className="stack">
        {opts.isLoading && <div className="muted">Загрузка…</div>}
        {opts.error && <Alert tone="error">Не удалось загрузить склады и товары. Закройте окно и попробуйте ещё раз.</Alert>}
        {noWarehouse && <Alert tone="warning">Сначала заведите склад: раздел «Склады»{o?.can.warehouses_create ? <> — <Link to="/warehouses">открыть</Link></> : ", попросите директора или кладовщика"}.</Alert>}
        {o && (
          <>
            <FilterChips value={v.kind} options={kinds} onChange={(k) => { f.set("kind", k); f.clear(); }} />
            {v.kind === "receipt" && (
              <FilterChips<Source> value={source} onChange={(x) => { setSource(x); f.clear(); }} options={[
                { id: "stock", label: "Товар со склада" }, { id: "catalog", label: "Из каталога деталей" },
                ...(o.can.items_create ? [{ id: "manual" as Source, label: "Новый вручную" }] : []),
              ]} />
            )}
            {isNew && source === "catalog" && (
              <Field label="Деталь из каталога" error={f.errors.item_id} hint="Если такого товара на складе ещё нет, он заведётся сам. Нет в каталоге — выберите «Новый вручную»">
                <CatalogPicker value={part} onPick={setPart} />
              </Field>
            )}
            {isNew && source === "manual" && (
              <div className="grid cols-2">
                <Field label="Наименование" error={f.errors.new_name ?? f.errors.name} full>
                  <input className="input" maxLength={200} value={manual.name} placeholder="Например: Фильтр масляный" onChange={(e) => setManual({ ...manual, name: e.target.value })} />
                </Field>
                <Field label="Артикул"><input className="input" maxLength={60} value={manual.sku} onChange={(e) => setManual({ ...manual, sku: e.target.value })} /></Field>
                <Field label="Единица" error={f.errors.new_unit}>
                  <select className="input" value={manual.unit} onChange={(e) => setManual({ ...manual, unit: e.target.value })}>{UNITS.map((u) => <option key={u} value={u}>{u}</option>)}</select>
                </Field>
              </div>
            )}
            {!isNew && (
            <Field label="Товар" error={f.errors.item_id} hint={v.kind !== "receipt" && !candidates.length ? "На складе нет товара в наличии: сначала оформите приход" : v.kind === "receipt" ? "Нужного товара нет в списке? Выберите выше «Из каталога деталей» или «Новый вручную»" : undefined}>
              <div className="stack-sm">
                <input className="input" placeholder="Найти по названию или артикулу" aria-label="Поиск товара" value={v.filter} onChange={(e) => f.set("filter", e.target.value)} />
                <select className="input" aria-label="Товар" value={v.item_id} onChange={(e) => f.set("item_id", e.target.value)}>
                  <option value="">— выберите товар —</option>
                  {candidates.map((i) => <option key={i.id} value={i.id}>{itemLabel(i, many, o.entities)}</option>)}
                </select>
              </div>
            </Field>
            )}
            <div className="grid cols-2">
              <Field label={v.kind === "transfer" ? "Откуда" : "Склад"} error={f.errors.warehouse_id}
                hint={item && v.kind !== "receipt" && v.warehouse_id ? `На этом складе: ${qtyAt(Number(v.warehouse_id))} ${unit}` : undefined}>
                <select className="input" value={v.warehouse_id} onChange={(e) => f.set("warehouse_id", e.target.value)}>
                  <option value="">— выберите склад —</option>
                  {whs.map((w) => <option key={w.id} value={w.id}>{w.name}{item && v.kind !== "receipt" ? ` — ${qtyAt(w.id)} ${unit}` : ""}</option>)}
                </select>
              </Field>
              {v.kind === "transfer" && (
                <Field label="Куда" error={f.errors.to_warehouse_id}>
                  <select className="input" value={v.to_warehouse_id} onChange={(e) => f.set("to_warehouse_id", e.target.value)}>
                    <option value="">— выберите склад —</option>
                    {whs.filter((w) => String(w.id) !== v.warehouse_id).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                  </select>
                </Field>
              )}
              <Field label={`Количество${unit ? `, ${unit}` : ""}`} error={f.errors.qty} hint="Можно дробное: 4,5">
                <input className="input" inputMode="decimal" autoComplete="off" value={v.qty} onChange={(e) => f.set("qty", e.target.value)} />
              </Field>
              {v.kind === "receipt" && (
                <Field label="Цена за единицу, ₽" error={f.errors.price} hint="Можно не указывать. По цене считается стоимость остатка и расход">
                  <input className="input" inputMode="decimal" autoComplete="off" value={v.price} onChange={(e) => f.set("price", e.target.value)} />
                </Field>
              )}
              <Field label="Дата" error={f.errors.move_date}>
                <input className="input" type="date" max={todayIso()} value={v.move_date} onChange={(e) => f.set("move_date", e.target.value)} />
              </Field>
              {v.kind === "receipt" && o.suppliers.length > 0 && (
                <Field label="Поставщик" error={f.errors.counterparty_id}>
                  <select className="input" value={v.counterparty_id} onChange={(e) => f.set("counterparty_id", e.target.value)}>
                    <option value="">— не указан —</option>
                    {o.suppliers.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                  </select>
                </Field>
              )}
              {(v.kind === "issue" || v.kind === "writeoff") && vehicles.length > 0 && (
                <Field label="На какую машину" error={f.errors.vehicle_id} hint="Можно не указывать">
                  <select className="input" value={v.vehicle_id} onChange={(e) => f.set("vehicle_id", e.target.value)}>
                    <option value="">— не указана —</option>
                    {vehicles.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
                  </select>
                </Field>
              )}
              <Field label={v.kind === "receipt" ? "Документ (УПД, накладная)" : "Документ"} error={f.errors.doc_number}>
                <input className="input" maxLength={60} value={v.doc_number} onChange={(e) => f.set("doc_number", e.target.value)} />
              </Field>
            </div>
            <Field label={v.kind === "writeoff" ? "Причина списания" : "Заметка"} error={f.errors.note}>
              <input className="input" maxLength={300} value={v.note} onChange={(e) => f.set("note", e.target.value)} />
            </Field>
            {v.kind !== "receipt" && <div className="muted small">Расход, списание и перемещение идут по средней закупочной цене. Больше, чем есть на складе, записать нельзя.</div>}
            {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
          </>
        )}
      </div>
    </Modal>
  );
}

/* ---------------- приём товара по счёту ---------------- */

interface Preview {
  bill: { id: number; number: string; bill_date: string; supplier: string; kind: string };
  lines: { pos: number; name: string; article: string; qty_text: string; unit: string; price: number; goods: boolean; item: { id: number; name: string; unit: string } | null; action: "existing" | "new" | "skip"; unit_clash: boolean }[];
  posted: number; can_post: boolean; reason: string;
}
const ACTION: Record<string, [string, Tone]> = { existing: ["есть на складе", "success"], new: ["новый товар", "info"], skip: ["не товар", "neutral"] };

export function ReceiveBillModal({ billId, onClose }: { billId: number; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const pre = useQuery({ queryKey: ["stock-bill", billId], queryFn: () => get<Preview>(`/stock/bill/${billId}`) });
  const opts = useStockOptions();
  const [wid, setWid] = useState("");
  const [error, setError] = useState("");
  const d = pre.data;
  const whs = opts.data?.warehouses ?? [];
  useEffect(() => { if (!wid && whs.length === 1) setWid(String(whs[0].id)); }, [whs]); // eslint-disable-line react-hooks/exhaustive-deps
  const m = useMutation({
    mutationFn: () => post<{ posted: number; created_items: number; skipped: string[] }>(`/stock/receive-bill/${billId}`, { warehouse_id: Number(wid) }),
    onSuccess: (r) => {
      qc.invalidateQueries();
      toast.success(`Принято на склад позиций: ${r.posted}${r.created_items ? `, новых товаров: ${r.created_items}` : ""}${r.skipped.length ? `. Пропущено: ${r.skipped.join("; ")}` : ""}`);
      onClose();
    },
    onError: (e: any) => setError(e.message),
  });
  return (
    <Modal title="Принять товар на склад по счёту" wide onClose={onClose}
      footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} disabled={!d?.can_post || !wid} onClick={() => { setError(""); m.mutate(); }}>Принять на склад</Button></>}>
      <div className="stack">
        {pre.isLoading && <div className="muted">Загрузка…</div>}
        {pre.error && <Alert tone="error">Не удалось открыть строки счёта.</Alert>}
        {d && (
          <>
            {d.reason && <Alert tone={d.posted ? "info" : "warning"}>{d.reason}{d.posted ? <> — <Link to={`/stock/moves?q=${encodeURIComponent("Счёт " + d.bill.number)}`}>посмотреть движения</Link></> : ""}</Alert>}
            <Table rows={d.lines} rowKey={(r) => r.pos}
              columns={[
                { key: "n", header: "Позиция", render: (r) => <>{r.name}{r.article && <span className="muted small"> · арт. {r.article}</span>}{r.item && r.item.name !== r.name && <div className="muted small">на складе: «{r.item.name}»</div>}</> },
                { key: "q", header: "Кол-во", num: true, render: (r) => `${r.qty_text} ${r.unit}` },
                { key: "p", header: "Цена, ₽", num: true, hideSm: true, render: (r) => fmtMoney(r.price) },
                { key: "a", header: "Что будет", render: (r) => r.unit_clash ? <Badge tone="warning">другая единица: «{r.item!.unit}»</Badge> : <Badge tone={ACTION[r.action][1]}>{ACTION[r.action][0]}</Badge> },
              ]} />
            <Field label="Склад, куда принять" hint="Цена на склад берётся из счёта как есть (с НДС). Услуги и работы (ч, усл.) пропускаются.">
              <select className="input" value={wid} onChange={(e) => setWid(e.target.value)} disabled={!d.can_post}>
                <option value="">— выберите склад —</option>
                {whs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </Field>
            {error && <Alert tone="error">{error}</Alert>}
          </>
        )}
      </div>
    </Modal>
  );
}
