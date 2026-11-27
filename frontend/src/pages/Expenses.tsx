// «Расходы»: всё, что компания потратила за месяц, одним экраном. Все затраты по машинам — одной строкой «Затраты на технику»
// (подробно — на странице «Затраты на спецтехнику»), отдельно счета без машины и закупки на склад, общие расходы и аренда.
// Итог совпадает с плиткой «Расходы за месяц» на главной.
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Info, Plus, Receipt, Truck } from "lucide-react";
import { get } from "../api";
import { useAuth } from "../auth";
import { fmtDate, fmtMoney, plural, todayIso } from "../format";
import { useEntityParam, useUrlState } from "../hooks";
import { Alert, Badge, Button, Card, Empty, PageHeader, QueryState, Table } from "../ui";

interface Cat { key: string; label: string; hint: string; counted: boolean; amount: number; count: number }
interface Row { cat: string; cat_label: string; counted: boolean; date: string; title: string; sub: string; amount: number; vehicle_id: number | null; vehicle: string; status: string; link: string; id: number }
interface Overview {
  month: string; title: string; total: number; prev_total: number; delta_pct: number | null; bills_unpaid: number;
  categories: Cat[]; items: Row[]; shown: number; found: number;
}

const shiftMonth = (m: string, delta: number) => { const [y, mo] = m.split("-").map(Number); const n = y * 12 + mo - 1 + delta; return `${Math.floor(n / 12)}-${String((n % 12) + 1).padStart(2, "0")}`; };

/** Куда ведёт «Открыть раздел» у каждой статьи и какое право для этого нужно. */
const OPEN: Record<string, { to: string; perm: string }> = {
  vehicles: { to: "/vehicle-finance", perm: "vehicle_finance.view" },
  bills_other: { to: "/bills", perm: "bills.view" },
  misc: { to: "/expenses/general", perm: "expenses.view" },
  rent_in: { to: "/rentals", perm: "rentals.view" },
  repairs: { to: "/vehicle-repairs", perm: "vehicle_repairs.view" },
};

export function Expenses() {
  const { can } = useAuth();
  const nav = useNavigate();
  const entity = useEntityParam();
  const [s, setS] = useUrlState({ month: "", cat: "" });
  const month = s.month || todayIso().slice(0, 7);
  const q = useQuery({
    queryKey: ["expenses-overview", month, s.cat, entity],
    queryFn: () => get<Overview>("/finance/expenses", { month, cat: s.cat || undefined, legal_entity_id: entity }),
    placeholderData: (p) => p,
  });
  const d = q.data;
  const counted = d?.categories.filter((c) => c.counted) ?? [];
  const withData = counted.filter((c) => c.count > 0 || c.amount !== 0);
  const empty = counted.filter((c) => !(c.count > 0 || c.amount !== 0));
  const reference = d?.categories.filter((c) => !c.counted && c.count > 0) ?? [];
  const chosen = d?.categories.find((c) => c.key === s.cat);
  const max = Math.max(1, ...withData.map((c) => c.amount));
  const toggle = (key: string) => setS({ cat: s.cat === key ? "" : key });

  return (
    <>
      <PageHeader
        title="Расходы"
        sub="Всё, что компания потратила за месяц. Затраты по машинам здесь одной строкой, подробно — в «Затратах на спецтехнику». Итог тот же, что на главной странице."
        actions={<>
          {can("bills.create") && <Link className="btn" to="/bills/new"><Plus size={16} aria-hidden /> Внести счёт</Link>}
          {can("expenses.create") && <Link className="btn" to="/expenses/general?new=1"><Plus size={16} aria-hidden /> Общий расход</Link>}
        </>}
      />
      <div className="toolbar">
        <div className="row" style={{ gap: 6 }}>
          <Button icon={ChevronLeft} aria-label="Прошлый месяц" onClick={() => setS({ month: shiftMonth(month, -1), cat: "" })} />
          <input className="input compact" type="month" aria-label="Месяц" value={month} onChange={(e) => e.target.value && setS({ month: e.target.value, cat: "" })} />
          <Button icon={ChevronRight} aria-label="Следующий месяц" onClick={() => setS({ month: shiftMonth(month, 1), cat: "" })} />
        </div>
      </div>
      <QueryState q={q}>
        {d && (
          <div className="stack">
            <div className="exp-kpis">
              <Card className="exp-total">
                <span className="muted small">Всего за {d.title}</span>
                <strong className="exp-big">{fmtMoney(d.total)} ₽</strong>
                <span className="exp-delta small">
                  {d.delta_pct === null
                    ? <span className="muted">в прошлом месяце расходов не было</span>
                    : <span className={d.delta_pct > 0 ? "bad" : "good"}>{d.delta_pct > 0 ? <ArrowUp size={13} aria-hidden /> : <ArrowDown size={13} aria-hidden />} {d.delta_pct > 0 ? "+" : ""}{d.delta_pct}% к прошлому месяцу ({fmtMoney(d.prev_total)} ₽)</span>}
                </span>
              </Card>
              <Card className={d.bills_unpaid > 0 ? "exp-warn" : ""}>
                <span className="muted small">Счета ждут оплаты</span>
                <strong className="exp-big">{fmtMoney(d.bills_unpaid)} ₽</strong>
                {can("bills.view") && <Link className="small" to={`/bills?month=${month}&status=unpaid`}>Открыть неоплаченные счета</Link>}
              </Card>
            </div>

            {withData.length === 0 ? (
              <Card><Empty icon={Receipt} title={`За ${d.title} расходов нет`}>Они появятся, когда внесут счета, заправки, штрафы, полисы или общие траты. Другой месяц можно выбрать выше.</Empty></Card>
            ) : (
              <section aria-label="Расходы по статьям">
                <h2 className="exp-h">По статьям <span className="muted small">нажмите на статью, чтобы увидеть её строки</span></h2>
                <div className="exp-cats">
                  {withData.map((c) => {
                    const open = OPEN[c.key];
                    return (
                      <div key={c.key} className={`exp-cat ${s.cat === c.key ? "on" : ""}`}>
                        <button type="button" className="exp-cat-main" aria-pressed={s.cat === c.key} onClick={() => toggle(c.key)}>
                          <span className="exp-cat-name">{c.label}</span>
                          <strong className="exp-cat-sum">{fmtMoney(c.amount)} ₽</strong>
                          <span className="exp-bar" aria-hidden><span style={{ width: `${Math.max(2, Math.round((c.amount / max) * 100))}%` }} /></span>
                          <span className="muted small">{d.total > 0 ? `${Math.round((c.amount / d.total) * 100)}% · ` : ""}{c.count} {plural(c.count, "запись", "записи", "записей")}</span>
                        </button>
                        {open && can(open.perm) && <Link className="exp-cat-open small" to={open.to}>Открыть раздел</Link>}
                      </div>
                    );
                  })}
                </div>
                {empty.length > 0 && <p className="muted small exp-none">В этом месяце записей нет: {empty.map((c) => c.label.toLowerCase()).join(", ")}.</p>}
              </section>
            )}

            <Card flush title={chosen ? chosen.label : "Все строки"} icon={Receipt}
              actions={chosen ? <Button size="sm" onClick={() => setS({ cat: "" })}>Показать все статьи</Button> : <span className="muted small">{d.found} {plural(d.found, "запись", "записи", "записей")}</span>}>
              {chosen && <p className="muted small exp-hint">{chosen.hint}</p>}
              {d.items.length === 0 ? <Empty icon={Receipt} title="Строк нет">За этот месяц по выбранной статье ничего не внесено.</Empty> : (
                <Table<Row>
                  rows={d.items}
                  rowKey={(r) => `${r.cat}-${r.id}`}
                  onRowClick={(r) => r.link && nav(r.link)}
                  dim={(r) => !r.counted}
                  columns={[
                    { key: "d", header: "Дата", render: (r) => <span className="nowrap">{fmtDate(r.date)}</span> },
                    { key: "c", header: "Статья", hideSm: true, render: (r) => <Badge tone={r.counted ? "neutral" : "info"}>{r.counted ? r.cat_label : "справочно"}</Badge> },
                    { key: "t", header: "Что", render: (r) => (
                      <div>
                        <strong>{r.title}</strong>
                        {r.sub && <div className="muted small">{r.sub}</div>}
                        {!r.counted && <div className="muted small">{r.cat_label}</div>}
                      </div>
                    ) },
                    { key: "v", header: "Машина", hideSm: true, render: (r) => r.vehicle ? <span className="nowrap"><Truck size={13} aria-hidden /> {r.vehicle}</span> : <span className="muted">{r.cat === "bills_other" ? "без привязки" : "—"}</span> },
                    { key: "s", header: "Состояние", hideSm: true, render: (r) => r.status === "unpaid" ? <Badge tone="warning">не оплачен</Badge> : r.status === "paid" ? <Badge tone="success">оплачен</Badge> : null },
                    { key: "a", header: "Сумма, ₽", num: true, render: (r) => <strong>{fmtMoney(r.amount)}</strong> },
                  ]}
                />
              )}
              {d.found > d.shown && <p className="muted small exp-hint">Показаны {d.shown} самых свежих из {d.found}. Остальные откройте в разделах статей.</p>}
            </Card>

            {reference.length > 0 && (
              <Alert tone="info">
                <Info size={14} aria-hidden /> Записи о ремонте техники со стоимостью ({reference.map((c) => `${fmtMoney(c.amount)} ₽`).join(", ")}) показаны справочно и в итог не входят: ремонт обычно уже внесён счётом, и сумма сложилась бы дважды.
              </Alert>
            )}
            <p className="muted small">Счета считаются по дате счёта: и оплаченные, и ждущие оплаты. Детали со склада входят в закупки на склад, а не в строку техники, чтобы не считать их дважды. Аренда — по суткам внутри месяца.</p>
          </div>
        )}
      </QueryState>
    </>
  );
}
