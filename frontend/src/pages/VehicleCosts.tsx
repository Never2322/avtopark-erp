// «Затраты на спецтехнику»: все затраты по машинам за месяц из всех разделов. Топливо (литры и сумма), ремонт по счетам и обращениям,
// детали со склада, штрафы, страховка и прочие затраты, внесённые вручную. Сверху итоги, ниже — по машинам и журнал всех операций.
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Fuel, Landmark, List, Package, PiggyBank, Plus, Receipt, ShieldCheck, Truck, Wrench, FileWarning, type LucideIcon } from "lucide-react";
import { get } from "../api";
import { fmtDate, fmtMoney, fmtNum, plural, todayIso } from "../format";
import { useEntityParam, useUrlState } from "../hooks";
import { Alert, Badge, Button, Card, Empty, FilterChips, PageHeader, QueryState, Table } from "../ui";

interface Cat { key: string; label: string; hint: string; amount: number; count: number }
interface VRow { vehicle_id: number; vehicle: string; liters: number; total: number; [k: string]: number | string }
interface Item { cat: string; cat_label: string; date: string; title: string; sub: string; qty: string; amount: number; vehicle_id: number | null; vehicle: string; link: string }
interface Data {
  month: string; title: string; total: number; liters: number; full: boolean; can_add: boolean;
  categories: Cat[]; vehicles: VRow[]; items: Item[]; found: number; vehicle_options: { id: number; label: string }[];
}

const ICON: Record<string, LucideIcon> = { fuel: Fuel, bills_net: Receipt, requests: Wrench, stock: Package, fines: FileWarning, insurance: ShieldCheck, other: Landmark };
const TONE: Record<string, string> = { fuel: "blue", bills_net: "mint", requests: "mint", stock: "amber", fines: "rose", insurance: "blue", other: "amber" };
const shiftMonth = (m: string, delta: number) => { const [y, mo] = m.split("-").map(Number); const n = y * 12 + mo - 1 + delta; return `${Math.floor(n / 12)}-${String((n % 12) + 1).padStart(2, "0")}`; };

export function VehicleCosts() {
  const nav = useNavigate();
  const entity = useEntityParam();
  const [s, setS] = useUrlState({ month: "", vehicle: "" });
  const [cat, setCat] = useState("all");
  const month = s.month || todayIso().slice(0, 7);
  const q = useQuery({
    queryKey: ["vehicle-costs", month, s.vehicle, entity],
    queryFn: () => get<Data>("/finance/vehicle-costs", { month, vehicle_id: s.vehicle || undefined, legal_entity_id: entity }),
    placeholderData: (p) => p,
  });
  const d = q.data;
  const cats = (d?.categories ?? []).filter((c) => c.amount > 0 || c.count > 0);
  const items = (d?.items ?? []).filter((x) => cat === "all" || x.cat === cat);
  return (
    <>
      <PageHeader title="Затраты на спецтехнику"
        sub="Все затраты по каждой машине за месяц: топливо, ремонт, детали со склада, штрафы, страховка и прочие траты из всех разделов программы"
        actions={<>
          {d?.can_add && <Link className="btn primary" to="/vehicle-finance/manual?new=1"><Plus size={16} aria-hidden /> Внести затрату</Link>}
          <Link className="btn" to="/vehicle-finance/manual"><List size={16} aria-hidden /> Внесённые вручную</Link>
        </>} />
      <div className="toolbar">
        <div className="row" style={{ gap: 6 }}>
          <Button icon={ChevronLeft} aria-label="Прошлый месяц" onClick={() => setS({ month: shiftMonth(month, -1) })} />
          <input className="input compact" type="month" aria-label="Месяц" value={month} onChange={(e) => e.target.value && setS({ month: e.target.value })} />
          <Button icon={ChevronRight} aria-label="Следующий месяц" onClick={() => setS({ month: shiftMonth(month, 1) })} />
        </div>
        <select className="input compact" aria-label="Машина" value={s.vehicle} onChange={(e) => setS({ vehicle: e.target.value })} style={{ maxWidth: 320 }}>
          <option value="">Вся техника</option>
          {d?.vehicle_options.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
        </select>
      </div>
      <QueryState q={q}>
        {d && (
          <div className="stack">
            {!d.full && <Alert tone="info">Топливо, счета, склад, штрафы и страховку видит тот, у кого есть право видеть деньги в отчётах. Вам показаны только затраты, внесённые вручную.</Alert>}
            <div className="crew-hero">
              <div className="card deco crew-tile tone-mint">
                <span className="crew-tile-ico" aria-hidden><PiggyBank size={20} /></span>
                <div className="deco-body">
                  <span className="deco-title">Всего за {d.title.toLowerCase()}</span>
                  <span className="deco-big">{fmtMoney(d.total)} <small>₽</small></span>
                  <span className="deco-sub">{d.vehicles.length} {plural(d.vehicles.length, "машина", "машины", "машин")} с затратами</span>
                </div>
              </div>
              {d.full && (
                <div className="card deco crew-tile tone-blue">
                  <span className="crew-tile-ico" aria-hidden><Fuel size={20} /></span>
                  <div className="deco-body">
                    <span className="deco-title">Топливо</span>
                    <span className="deco-big">{fmtNum(d.liters, 0)} <small>л</small></span>
                    <span className="deco-sub">{fmtMoney(d.categories.find((c) => c.key === "fuel")?.amount ?? 0)} ₽</span>
                  </div>
                </div>
              )}
            </div>

            {cats.length === 0 ? (
              <Card><Empty icon={PiggyBank} title={`За ${d.title.toLowerCase()} затрат нет`}>Затраты появятся, когда внесут заправки, счета, выдачу деталей со склада, штрафы, полисы или прочие траты.</Empty></Card>
            ) : (
              <>
                <div className="vc-cats">
                  {cats.map((c) => {
                    const Icon = ICON[c.key] ?? Receipt;
                    return (
                      <button key={c.key} type="button" className={`card vc-cat tone-${TONE[c.key] ?? "mint"}${cat === c.key ? " on" : ""}`} aria-pressed={cat === c.key}
                        title={c.hint} onClick={() => setCat(cat === c.key ? "all" : c.key)}>
                        <span className="vc-cat-ico" aria-hidden><Icon size={16} /></span>
                        <span className="vc-cat-name">{c.label}</span>
                        <strong>{fmtMoney(c.amount)} ₽</strong>
                        <span className="muted small">{d.total > 0 ? `${Math.round((c.amount / d.total) * 100)}% · ` : ""}{c.count} {plural(c.count, "запись", "записи", "записей")}</span>
                      </button>
                    );
                  })}
                </div>

                <Card flush title="По машинам" icon={Truck}>
                  <Table<VRow>
                    rows={d.vehicles}
                    rowKey={(r) => String(r.vehicle_id)}
                    onRowClick={(r) => setS({ vehicle: s.vehicle === String(r.vehicle_id) ? "" : String(r.vehicle_id) })}
                    columns={[
                      { key: "v", header: "Машина", render: (r) => <strong>{r.vehicle}</strong> },
                      ...(d.full ? [{ key: "l", header: "Топливо, л", num: true, render: (r: VRow) => (r.liters ? fmtNum(r.liters, 1) : "—") }] : []),
                      ...cats.map((c) => ({
                        key: c.key, header: `${c.label}, ₽`, num: true, hideSm: c.key !== "fuel",
                        render: (r: VRow) => (Number(r[c.key]) ? fmtMoney(Number(r[c.key])) : <span className="muted">—</span>),
                      })),
                      { key: "t", header: "Всего, ₽", num: true, render: (r) => <strong>{fmtMoney(r.total)}</strong> },
                    ]}
                  />
                </Card>

                <Card flush title="Все операции" icon={Receipt}
                  actions={<FilterChips options={[{ id: "all", label: "Все", count: d.found }, ...cats.map((c) => ({ id: c.key, label: c.label, count: c.count }))]} value={cat} onChange={setCat} />}>
                  {items.length === 0 ? <Empty icon={Receipt} title="Операций нет" /> : (
                    <Table<Item>
                      rows={items}
                      rowKey={(r) => `${r.cat}-${r.date}-${r.title}-${r.amount}-${r.vehicle_id}`}
                      onRowClick={(r) => r.link && nav(r.link)}
                      columns={[
                        { key: "d", header: "Дата", render: (r) => <span className="nowrap">{fmtDate(r.date)}</span> },
                        { key: "c", header: "Статья", hideSm: true, render: (r) => <Badge>{r.cat_label}</Badge> },
                        { key: "t", header: "Что", render: (r) => <div><strong>{r.title}</strong>{r.sub && <div className="muted small">{r.sub}</div>}</div> },
                        { key: "q", header: "Количество", hideSm: true, render: (r) => r.qty || <span className="muted">—</span> },
                        { key: "v", header: "Машина", hideSm: true, render: (r) => <span className="nowrap">{r.vehicle}</span> },
                        { key: "a", header: "Сумма, ₽", num: true, render: (r) => <strong>{fmtMoney(r.amount)}</strong> },
                      ]}
                    />
                  )}
                  {d.found > d.items.length && <p className="muted small exp-hint">Показаны {d.items.length} самых свежих из {d.found}. Выберите машину, чтобы увидеть всё по ней.</p>}
                </Card>
              </>
            )}
          </div>
        )}
      </QueryState>
    </>
  );
}
