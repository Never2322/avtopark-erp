// Склад: остатки по товарам и складам, что заканчивается, быстрые кнопки прихода и расхода.
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, Boxes, Download, PackageMinus, PackagePlus, Search, Trash2 } from "lucide-react";
import { get, qs } from "../api";
import { fmtMoney } from "../format";
import { useDebounced, useEntityParam, useUrlState } from "../hooks";
import { StockMoveModal, useStockOptions } from "../components/stock";
import { Badge, Button, Card, Empty, FilterChips, PageHeader, QueryState, Table } from "../ui";

interface Row {
  item_id: number; name: string; sku: string; unit: string; legal_entity_id: number; legal_entity: string; min_qty: number; min_text: string;
  total: number; total_text: string; qty: number; qty_text: string; low: boolean; avg_price: number | null; value: number | null;
  places: { warehouse_id: number; warehouse: string; qty: number; qty_text: string }[];
}
interface Data { items: Row[]; totals: { positions: number; low: number; value: number }; can: { create: boolean; view_moves: boolean } }

export function Stock() {
  const nav = useNavigate();
  const entity = useEntityParam();
  const opts = useStockOptions(entity);
  const [s, setS] = useUrlState({ q: "", wh: "", low: "" });
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setText(s.q); }, [s.q]);
  const [modal, setModal] = useState<{ kind: string; itemId?: number } | null>(null);

  const params = { q: s.q, warehouse_id: s.wh, low: s.low === "1", legal_entity_id: entity };
  const list = useQuery({ queryKey: ["stock", params], queryFn: () => get<Data>("/stock/balances", params), placeholderData: (p) => p });
  const d = list.data;
  const o = opts.data;
  const canMove = !!d?.can.create;
  const filtered = !!(s.q || s.wh || s.low);
  const many = !!o && Object.keys(o.entities).length > 1;

  return (
    <>
      <PageHeader title="Склад" sub={d ? `Позиций: ${d.totals.positions}${d.totals.value ? ` · стоимость остатков ${fmtMoney(d.totals.value)} ₽ по средней закупочной цене` : ""}` : undefined}
        actions={<>
          {d && <a className="btn" href={`/api/stock/balances/export.csv${qs(params)}`}><Download size={16} aria-hidden /> В Excel</a>}
          {canMove && <Button icon={PackageMinus} onClick={() => setModal({ kind: "issue" })}>Расход</Button>}
          {canMove && <Button icon={Trash2} onClick={() => setModal({ kind: "writeoff" })}>Списание</Button>}
          {canMove && <Button icon={ArrowLeftRight} onClick={() => setModal({ kind: "transfer" })}>Перемещение</Button>}
          {canMove && <Button variant="primary" icon={PackagePlus} onClick={() => setModal({ kind: "receipt" })}>Приход</Button>}
        </>} />
      <Card flush>
        <div className="toolbar" style={{ padding: 14, marginBottom: 0 }}>
          <div style={{ position: "relative", minWidth: 220, flex: "1 1 220px", maxWidth: 360 }}>
            <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
            <input className="input" style={{ paddingLeft: 34 }} placeholder="Название или артикул" aria-label="Поиск по складу" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <select className="input compact" aria-label="Склад" value={s.wh} onChange={(e) => setS({ wh: e.target.value })}>
            <option value="">Все склады</option>
            {o?.warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}{many ? ` (${o.entities[String(w.legal_entity_id)] ?? ""})` : ""}</option>)}
          </select>
          <FilterChips value={s.low} onChange={(v) => setS({ low: v })} options={[{ id: "", label: "Все" }, { id: "1", label: "Заканчивается", count: d?.totals.low }]} />
        </div>
        <QueryState q={list}>
          {d && (d.items.length === 0 ? (
            <Empty icon={Boxes} title={filtered ? "Ничего не найдено" : "На складе пока пусто"}>
              {filtered ? <Button onClick={() => { setText(""); setS({ q: "", wh: "", low: "" }); }}>Сбросить фильтры</Button>
                : o && o.warehouses.length === 0 ? <>Сначала заведите склад (раздел «Склады»){o.can.warehouses_create ? <> — <Link to="/warehouses">открыть</Link></> : ""}, потом товары и приход.</>
                : o && o.items.length === 0 ? <>Заведите товары в разделе «Товары»{o.can.items_create ? <> — <Link to="/stock-items">открыть</Link></> : ""}, потом оформите приход.</>
                : "Оформите приход: товар появится здесь."}
            </Empty>
          ) : (
            <Table rows={d.items} rowKey={(r) => r.item_id} onRowClick={d.can.view_moves ? (r) => nav(`/stock/moves?item=${r.item_id}`) : undefined}
              columns={[
                { key: "n", header: "Товар", render: (r) => (
                  <>
                    <strong>{r.name}</strong>{r.sku && <span className="muted small"> · {r.sku}</span>}{many && !entity && <span className="muted small"> · {r.legal_entity}</span>}
                    {r.places.length > 0 && <div className="muted small">{r.places.map((p) => `${p.warehouse}: ${p.qty_text}`).join(" · ")}</div>}
                  </>) },
                { key: "q", header: s.wh ? "На складе" : "Всего", num: true, render: (r) => (
                  <>
                    <strong>{r.qty_text} {r.unit}</strong>
                    {r.low && <div><Badge tone="warning">заканчивается</Badge></div>}
                  </>) },
                { key: "m", header: "Минимум", num: true, hideSm: true, render: (r) => (r.min_qty > 0 ? `${r.min_text} ${r.unit}` : <span className="muted">—</span>) },
                { key: "p", header: "Цена, ₽", num: true, hideSm: true, render: (r) => (r.avg_price === null ? <span className="muted">—</span> : fmtMoney(r.avg_price)) },
                { key: "v", header: "Сумма, ₽", num: true, hideSm: true, render: (r) => (r.value === null ? <span className="muted">—</span> : fmtMoney(r.value)) },
                ...(canMove ? [{ key: "a", header: "", width: 120, render: (r: Row) => (
                  <span className="row" style={{ gap: 4, justifyContent: "flex-end" }} onClick={(e) => e.stopPropagation()}>
                    <Button size="sm" variant="ghost" icon={PackagePlus} aria-label={`Приход: ${r.name}`} title="Приход" onClick={() => setModal({ kind: "receipt", itemId: r.item_id })} />
                    <Button size="sm" variant="ghost" icon={PackageMinus} aria-label={`Расход: ${r.name}`} title="Расход" disabled={r.total <= 0} onClick={() => setModal({ kind: "issue", itemId: r.item_id })} />
                  </span>) }] : []),
              ]} />
          ))}
        </QueryState>
      </Card>
      {d && d.items.some((r) => r.avg_price === null && r.total > 0) && <div className="muted small" style={{ marginTop: 10 }}>У части товаров нет цены: приход без цены в стоимость остатков не входит.</div>}
      {modal && <StockMoveModal kind={modal.kind} itemId={modal.itemId} entity={entity} onClose={() => setModal(null)} />}
    </>
  );
}
