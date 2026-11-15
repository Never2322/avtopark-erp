// Движение товара: журнал прихода, расхода, списаний и перемещений. Ошибочное движение убирают в архив с причиной — остаток пересчитается сам.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, ArrowLeftRight, Download, Pencil, Search } from "lucide-react";
import { get, patch, post, qs } from "../api";
import { useAuth } from "../auth";
import { fmtDate, fmtMoney, todayIso } from "../format";
import { Page, useDebounced, useEntityParam, useUrlState } from "../hooks";
import { KIND_TONE, StockMoveModal, useStockOptions } from "../components/stock";
import { Alert, Badge, Button, Card, ConfirmModal, Empty, Field, FilterChips, Modal, PageHeader, Pagination, QueryState, Table, useForm, useToast } from "../ui";

interface Move {
  id: number; legal_entity_id: number; legal_entity: string; move_date: string; kind: string; kind_label: string; item_id: number; item: string; sku: string; unit: string;
  warehouse: string; to_warehouse: string; qty_text: string; price: number; amount: number; counterparty_id: number | null; counterparty: string;
  vehicle_id: number | null; vehicle: string; request_id: number | null; request_number: number | null; bill_id: number | null; bill_number: string | null;
  doc_number: string; note: string; archived: boolean; archive_reason: string; archived_by: string | null; created_by: string | null;
  can_edit: boolean; can_void: boolean; can_restore: boolean;
}
interface MovePage extends Page<Move> { sums: Record<string, number> }

function EditModal({ m, onClose }: { m: Move; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const opts = useStockOptions();
  const f = useForm({ move_date: m.move_date, price: m.price ? String(m.price).replace(".", ",") : "", doc_number: m.doc_number, note: m.note,
    counterparty_id: m.counterparty_id ? String(m.counterparty_id) : "", vehicle_id: m.vehicle_id ? String(m.vehicle_id) : "" });
  const v = f.values;
  const save = useMutation({
    mutationFn: () => patch(`/stock/moves/${m.id}`, {
      move_date: v.move_date, price: v.price.trim() || undefined, doc_number: v.doc_number, note: v.note,
      ...(m.kind === "receipt" ? { counterparty_id: v.counterparty_id ? Number(v.counterparty_id) : null } : {}),
      ...(m.kind === "issue" || m.kind === "writeoff" ? { vehicle_id: v.vehicle_id ? Number(v.vehicle_id) : null } : {}),
    }),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Описание сохранено"); onClose(); },
    onError: f.fail,
  });
  const vehicles = (opts.data?.vehicles ?? []).filter((x) => x.legal_entity_id === m.legal_entity_id);
  return (
    <Modal title="Изменить описание движения" onClose={onClose} footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={save.isPending} onClick={() => { f.clear(); save.mutate(); }}>Сохранить</Button></>}>
      <div className="stack">
        <Alert tone="info">{m.kind_label}: {m.item}, {m.qty_text} {m.unit}. Количество, товар и склад не меняются: если ошиблись, уберите движение в архив и запишите заново.</Alert>
        <div className="grid cols-2">
          <Field label="Дата" error={f.errors.move_date}><input className="input" type="date" max={todayIso()} value={v.move_date} onChange={(e) => f.set("move_date", e.target.value)} /></Field>
          <Field label="Цена за единицу, ₽" error={f.errors.price}><input className="input" inputMode="decimal" value={v.price} onChange={(e) => f.set("price", e.target.value)} /></Field>
          {m.kind === "receipt" && (
            <Field label="Поставщик" error={f.errors.counterparty_id}>
              <select className="input" value={v.counterparty_id} onChange={(e) => f.set("counterparty_id", e.target.value)}>
                <option value="">— не указан —</option>
                {opts.data?.suppliers.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </Field>
          )}
          {(m.kind === "issue" || m.kind === "writeoff") && (
            <Field label="Машина" error={f.errors.vehicle_id}>
              <select className="input" value={v.vehicle_id} onChange={(e) => f.set("vehicle_id", e.target.value)}>
                <option value="">— не указана —</option>
                {vehicles.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
              </select>
            </Field>
          )}
          <Field label="Документ" error={f.errors.doc_number}><input className="input" maxLength={60} value={v.doc_number} onChange={(e) => f.set("doc_number", e.target.value)} /></Field>
        </div>
        <Field label="Заметка" error={f.errors.note}><input className="input" maxLength={300} value={v.note} onChange={(e) => f.set("note", e.target.value)} /></Field>
        {f.formError && !Object.keys(f.errors).length && <Alert tone="error">{f.formError}</Alert>}
      </div>
    </Modal>
  );
}

export function StockMoves() {
  const entity = useEntityParam();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const opts = useStockOptions(entity);
  const o = opts.data;
  const [s, setS] = useUrlState({ q: "", kind: "", month: "", wh: "", item: "", archived: "", page: "1" });
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setText(s.q); }, [s.q]);
  const [modal, setModal] = useState<boolean>(false);
  const [edit, setEdit] = useState<Move | null>(null);
  const [void_, setVoid] = useState<Move | null>(null);

  const params = { q: s.q, kind: s.kind, month: s.month, warehouse_id: s.wh, item_id: s.item, archived: s.archived === "1", legal_entity_id: entity, page: Number(s.page), page_size: 25 };
  const list = useQuery({ queryKey: ["stock-moves", params], queryFn: () => get<MovePage>("/stock/moves", params), placeholderData: (p) => p });
  const d = list.data;
  const filtered = !!(s.q || s.kind || s.month || s.wh || s.item || s.archived);
  const many = !!o && Object.keys(o.entities).length > 1;
  const restore = useMutation({
    mutationFn: (id: number) => post(`/stock/moves/${id}/restore`),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Движение возвращено, остаток пересчитан"); },
    onError: (e: any) => toast.error(e.message),
  });
  const itemName = o?.items.find((i) => String(i.id) === s.item)?.name;

  return (
    <>
      <PageHeader title="Движение товара" sub="Приход, расход, списания и перемещения. Остаток считается по этим записям"
        actions={<>
          {d && <a className="btn" href={`/api/stock/moves/export.csv${qs({ ...params, page: undefined, page_size: undefined })}`}><Download size={16} aria-hidden /> В Excel</a>}
          {o?.can.create && <Button variant="primary" icon={ArrowLeftRight} onClick={() => setModal(true)}>Новое движение</Button>}
        </>} />
      <Card flush>
        <div className="toolbar" style={{ padding: "14px 14px 0" }}>
          <div style={{ position: "relative", minWidth: 220, flex: "1 1 220px", maxWidth: 340 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Товар, артикул, документ, заметка" aria-label="Поиск по движениям" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <input className="input compact" type="month" aria-label="Месяц" value={s.month} onChange={(e) => setS({ month: e.target.value })} />
          <select className="input compact" aria-label="Склад" value={s.wh} onChange={(e) => setS({ wh: e.target.value })}>
            <option value="">Все склады</option>
            {o?.warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}{many ? ` (${o.entities[String(w.legal_entity_id)] ?? ""})` : ""}</option>)}
          </select>
          <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={s.archived === "1"} onChange={(e) => setS({ archived: e.target.checked ? "1" : "" })} /> <span>Архив</span></label>
        </div>
        <div style={{ padding: "12px 14px" }}>
          <FilterChips value={s.kind} onChange={(v) => setS({ kind: v })} options={[{ id: "", label: "Все" }, ...(o?.kinds.map((k) => ({ id: k.code, label: k.label })) ?? [])]} />
          {s.item && <div className="row" style={{ marginTop: 10, gap: 8 }}><span className="muted">Только товар:</span><Badge tone="info">{itemName ?? `№ ${s.item}`}</Badge><Button size="sm" variant="ghost" onClick={() => setS({ item: "" })}>Показать все</Button></div>}
        </div>
        {d && !s.archived && (d.sums.receipt || d.sums.issue || d.sums.writeoff) ? (
          <div className="bill-totals">
            <div><span className="muted small">Приход, ₽</span><strong>{fmtMoney(d.sums.receipt)}</strong></div>
            <div><span className="muted small">Расход, ₽</span><strong>{fmtMoney(d.sums.issue)}</strong></div>
            <div><span className="muted small">Списано, ₽</span><strong>{fmtMoney(d.sums.writeoff)}</strong></div>
          </div>
        ) : null}
        <QueryState q={list}>
          {d && (d.items.length === 0 ? (
            <Empty icon={ArrowLeftRight} title={filtered ? "Ничего не найдено" : "Движений пока нет"}>
              {filtered ? <Button onClick={() => { setText(""); setS({ q: "", kind: "", month: "", wh: "", item: "", archived: "" }); }}>Сбросить фильтры</Button> : "Оформите первый приход: остатки появятся на странице «Склад»."}
            </Empty>
          ) : (
            <>
              <Table rows={d.items} rowKey={(r) => r.id} dim={(r) => r.archived}
                columns={[
                  { key: "d", header: "Дата", render: (r) => fmtDate(r.move_date) },
                  { key: "k", header: "Вид", render: (r) => <Badge tone={KIND_TONE[r.kind] ?? "neutral"}>{r.kind_label}</Badge> },
                  { key: "i", header: "Товар", render: (r) => (
                    <>
                      <strong>{r.item}</strong>{r.sku && <span className="muted small"> · {r.sku}</span>}{many && !entity && <span className="muted small"> · {r.legal_entity}</span>}
                      <div className="muted small">{r.warehouse}{r.to_warehouse ? ` → ${r.to_warehouse}` : ""}</div>
                      {r.archived && <div className="small" style={{ color: "var(--warning)" }}>В архиве: {r.archive_reason}{r.archived_by ? `, ${r.archived_by}` : ""}</div>}
                    </>) },
                  { key: "q", header: "Кол-во", num: true, render: (r) => <strong>{r.qty_text} {r.unit}</strong> },
                  { key: "s", header: "Сумма, ₽", num: true, hideSm: true, render: (r) => (r.amount ? fmtMoney(r.amount) : <span className="muted">—</span>) },
                  { key: "l", header: "Связано", hideSm: true, render: (r) => (
                    <>
                      {r.counterparty && <div>{r.counterparty}</div>}
                      {r.vehicle && <div>{r.vehicle}</div>}
                      {r.request_id && (can("requests.view") || can("requests.view_own") ? <div><Link to={`/requests/${r.request_id}`}>обращение № {r.request_number}</Link></div> : <div>обращение № {r.request_number}</div>)}
                      {r.bill_id && (can("bills.view") ? <div><Link to={`/bills/${r.bill_id}`}>счёт № {r.bill_number}</Link></div> : <div>счёт № {r.bill_number}</div>)}
                      {r.doc_number && !r.bill_id && <div className="muted small">{r.doc_number}</div>}
                      {r.note && <div className="muted small">{r.note}</div>}
                    </>) },
                  { key: "a", header: "", width: 96, render: (r) => (
                    <span className="row" style={{ gap: 2, justifyContent: "flex-end" }}>
                      {r.can_edit && <Button size="sm" variant="ghost" icon={Pencil} aria-label="Изменить описание" title="Изменить описание" onClick={() => setEdit(r)} />}
                      {r.can_void && <Button size="sm" variant="ghost" icon={Archive} aria-label="Убрать в архив" title="Убрать в архив" onClick={() => setVoid(r)} />}
                      {r.can_restore && <Button size="sm" variant="ghost" icon={ArchiveRestore} aria-label="Вернуть из архива" title="Вернуть из архива" loading={restore.isPending && restore.variables === r.id} onClick={() => restore.mutate(r.id)} />}
                    </span>) },
                ]} />
              <Pagination page={Number(s.page)} pageSize={25} total={d.total} onPage={(p) => setS({ page: String(p) })} />
            </>
          ))}
        </QueryState>
      </Card>
      {modal && <StockMoveModal entity={entity} itemId={s.item ? Number(s.item) : undefined} onClose={() => setModal(false)} />}
      {edit && <EditModal m={edit} onClose={() => setEdit(null)} />}
      {void_ && (
        <ConfirmModal title="Убрать движение в архив?" danger confirmLabel="В архив" reasonLabel="Причина"
          text={<>{void_.kind_label}: «{void_.item}», {void_.qty_text} {void_.unit}. Остаток пересчитается. Движение не стирается: его можно вернуть. Если после этого остаток стал бы меньше нуля, программа не даст.</>}
          onConfirm={async (reason) => { await post(`/stock/moves/${void_.id}/void`, { reason }); qc.invalidateQueries(); toast.success("Движение убрано в архив, остаток пересчитан"); }} onClose={() => setVoid(null)} />
      )}
    </>
  );
}
