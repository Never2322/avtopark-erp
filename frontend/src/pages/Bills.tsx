// Счета на ремонт: список по месяцам, сводка «сколько потратили» и выгрузка в Excel.
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FilePlus2, FileUp, Paperclip, ReceiptText, Search, Upload } from "lucide-react";
import { errorText, get, post, put, qs } from "../api";
import { fmtDate, fmtMoney } from "../format";
import { Page, useDebounced, useEntityParam, useUrlState } from "../hooks";
import { useVehicleOptions } from "../components/pickers";
import { Badge, Button, Card, Empty, FilterChips, PageHeader, Pagination, QueryState, Table, Tabs, useToast } from "../ui";
import { BillAuto } from "./BillAuto";

export interface BillRow {
  id: number; legal_entity_id: number; legal_entity: string; kind: string; kind_label: string; number: string; bill_date: string; supplier_name: string; supplier_inn: string;
  basis: string; vehicle_id: number | null; vehicle: string; request_id: number | null; request_number: number | null; total: number; vat: number;
  status: string; status_label: string; paid_date: string | null; files: number; archived: boolean; category: string | null; category_label: string;
}
interface BudgetItem { code: string; label: string; budget: number | null; spent: number; left: number | null }
interface Budget { month: string; items: BudgetItem[]; none: number; can_edit: boolean }
interface Totals { count: number; total: number; paid: number; unpaid: number; vat: number; cancelled: number }
interface BillPage extends Page<BillRow> { totals: Totals; month: string; budget: Budget | null }
export interface BillMeta {
  categories: { code: string; label: string }[]; statuses: { code: string; label: string }[]; kinds: { code: string; label: string }[]; vat_rates: { value: number; label: string }[]; units: string[];
  months: string[]; month_labels: Record<string, string>; default_month: string; entities: { id: number; name: string }[];
  can: { create: boolean; export: boolean; pay: boolean; edit: boolean; void: boolean; catalog: boolean; purchases: boolean };
}
export const useBillMeta = () => useQuery({ queryKey: ["bills-meta"], queryFn: () => get<BillMeta>("/bills/meta"), staleTime: 60_000 });

export function BillStatus({ code, label }: { code: string; label: string }) {
  return <Badge tone={code === "paid" ? "success" : code === "cancelled" ? "neutral" : "warning"}>{label}</Badge>;
}

/** Бюджет на месяц по категории: сумму вписывает директор, счета этой категории за месяц вычитаются из неё. */
function BudgetBox({ b, month, can, onSaved }: { b: BudgetItem; month: string; can: boolean; onSaved: () => void }) {
  const toast = useToast();
  const [v, setV] = useState<string | null>(null);
  const shown = v ?? (b.budget != null ? String(b.budget).replace(".", ",") : "");
  const save = async () => {
    if (v === null) return;
    const before = b.budget != null ? String(b.budget).replace(".", ",") : "";
    if (v.trim() === before) { setV(null); return; }
    try { await put("/bills/budget", { month, category: b.code, amount: v.trim() || null }); onSaved(); } catch (e) { toast.error(errorText(e)); } finally { setV(null); }
  };
  const over = b.left != null && b.left < 0;
  return (
    <div className={`budget-box cat-${b.code} ${over ? "over" : ""}`}>
      <span className="budget-title">{b.label}</span>
      {can ? (
        <label className="budget-input">
          <span className="muted small">Выделено на месяц, ₽</span>
          <input className="input compact" inputMode="decimal" placeholder="впишите сумму" value={shown} aria-label={`Бюджет на ${b.label.toLowerCase()}, ₽`}
            onChange={(e) => setV(e.target.value)} onBlur={save} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") setV(null); }} />
        </label>
      ) : <span className="muted small">Выделено: {b.budget != null ? `${fmtMoney(b.budget)} ₽` : "не задано"}</span>}
      <span className="budget-line"><span className="muted small">Потрачено</span><strong>{fmtMoney(b.spent)} ₽</strong></span>
      <span className="budget-line"><span className="muted small">{over ? "Перерасход" : "Осталось"}</span>
        <strong className="budget-left">{b.left == null ? "—" : `${fmtMoney(Math.abs(b.left))} ₽`}</strong></span>
      {b.budget ? <div className="budget-bar"><span style={{ width: `${Math.min(100, Math.round((b.spent / b.budget) * 100))}%` }} /></div> : null}
    </div>
  );
}

interface SummaryRow { key: string | null; label: string; count: number; total: number; paid: number; unpaid: number; vat: number }
interface Summary { group: string; date_from: string; date_to: string; items: SummaryRow[]; totals: { count: number; total: number; paid: number; unpaid: number; vat: number } }

function SummaryTab({ entity, vehicle }: { entity?: number; vehicle: string }) {
  const [group, setGroup] = useState<"month" | "vehicle">("month");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const params = { group, date_from: from, date_to: to, legal_entity_id: entity, vehicle_id: vehicle };
  const q = useQuery({ queryKey: ["bills-summary", params], queryFn: () => get<Summary>("/bills/summary", params), placeholderData: (p) => p });
  const d = q.data;
  const max = Math.max(1, ...(d?.items.map((i) => i.total) ?? [1]));
  return (
    <>
      <div className="toolbar" style={{ padding: "0 14px" }}>
        <select className="input compact" aria-label="Группировка" value={group} onChange={(e) => setGroup(e.target.value as "month" | "vehicle")}>
          <option value="month">По месяцам</option><option value="vehicle">По машинам</option>
        </select>
        <label className="row" style={{ gap: 6 }}><span className="muted small">с</span><input className="input compact" type="date" aria-label="С даты" value={from || d?.date_from || ""} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="row" style={{ gap: 6 }}><span className="muted small">по</span><input className="input compact" type="date" aria-label="По дату" value={to || d?.date_to || ""} onChange={(e) => setTo(e.target.value)} /></label>
      </div>
      <QueryState q={q}>
        {d && (d.items.length === 0 ? <Empty icon={ReceiptText} title="За этот период счетов нет">Отменённые счета и счета в архиве в сводку не входят.</Empty> : (
          <Table rows={d.items} rowKey={(r) => r.key ?? "none"}
            columns={[
              { key: "l", header: group === "month" ? "Месяц" : "Машина", render: (r) => <strong>{r.label}</strong> },
              { key: "b", header: "Доля", hideSm: true, width: 160, render: (r) => <div className="bar"><span style={{ width: `${Math.round((r.total / max) * 100)}%` }} /></div> },
              { key: "c", header: "Счетов", num: true, render: (r) => r.count },
              { key: "t", header: "Сумма, ₽", num: true, render: (r) => <strong>{fmtMoney(r.total)}</strong> },
              { key: "p", header: "Оплачено, ₽", num: true, hideSm: true, render: (r) => fmtMoney(r.paid) },
              { key: "u", header: "Ждёт оплаты, ₽", num: true, render: (r) => (r.unpaid ? <span style={{ color: "var(--warning)" }}>{fmtMoney(r.unpaid)}</span> : fmtMoney(0)) },
              { key: "v", header: "в т.ч. НДС, ₽", num: true, hideSm: true, render: (r) => fmtMoney(r.vat) },
            ]} />
        ))}
        {d && d.items.length > 0 && (
          <div className="row wrap sum-line">
            <span>Итого за период: <strong>{fmtMoney(d.totals.total)} ₽</strong> ({d.totals.count} шт.)</span>
            <span className="muted">оплачено {fmtMoney(d.totals.paid)} ₽ · ждёт оплаты {fmtMoney(d.totals.unpaid)} ₽ · НДС {fmtMoney(d.totals.vat)} ₽</span>
          </div>
        )}
      </QueryState>
    </>
  );
}

export function Bills() {
  const nav = useNavigate();
  const entity = useEntityParam();
  const meta = useBillMeta();
  const [s, setS] = useUrlState({ q: "", month: "", cat: "", vehicle: "", archived: "", tab: "list", page: "1" });
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState(s.q);
  const [auto, setAuto] = useState(false);
  const debounced = useDebounced(text);
  const vehicles = useVehicleOptions(entity);
  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setText(s.q); }, [s.q]);

  const month = s.month || meta.data?.default_month || "";
  const params = { q: s.q, month, category: s.cat, vehicle_id: s.vehicle, archived: s.archived === "1", legal_entity_id: entity, page: Number(s.page), page_size: 25 };
  const list = useQuery({ queryKey: ["bills", params], queryFn: () => get<BillPage>("/bills", params), enabled: !!month && s.tab === "list", placeholderData: (p) => p });
  const d = list.data;
  const can = meta.data?.can;
  const filtered = !!(s.q || s.cat || s.vehicle || s.archived);
  const refresh = () => qc.invalidateQueries({ queryKey: ["bills"] });
  const setCategory = (r: BillRow, category: string) =>
    post(`/bills/${r.id}/category`, { category: category || null }).then(refresh).catch((e) => toast.error(errorText(e)));

  return (
    <>
      <PageHeader title="Счета на ремонт" sub="Что выставили поставщики за запчасти и работы, сколько заплатили и сколько ещё должны"
        actions={<>
          {can?.export && s.tab === "list" && <a className="btn" href={`/api/bills/export.xlsx${qs({ ...params, page: undefined, page_size: undefined })}`}><Download size={16} aria-hidden /> В Excel</a>}
          {can?.create && <Button variant="primary" icon={FileUp} onClick={() => setAuto(true)}>Загрузить счёт</Button>}
          {can?.create && <Button icon={Upload} onClick={() => nav("/bills/new?kind=uploaded")}>Внести вручную</Button>}
          {can?.create && <Button icon={FilePlus2} onClick={() => nav("/bills/new?kind=issued")}>Выписать счёт</Button>}
        </>} />
      <Tabs tabs={[{ id: "list", label: "Счета" }, { id: "summary", label: "Сколько потратили" }]} value={s.tab as "list" | "summary"} onChange={(v) => setS({ tab: v })} />
      <Card flush>
        <div className="toolbar" style={{ padding: "14px 14px 0" }}>
          {s.tab === "list" && (
            <>
              <div style={{ position: "relative", minWidth: 220, flex: "1 1 220px", maxWidth: 340 }}>
                <Search size={16} aria-hidden style={{ position: "absolute", left: 11, top: 11, color: "var(--text-2)" }} />
                <input className="input" style={{ paddingLeft: 34 }} placeholder="Номер, поставщик, ИНН, госномер" aria-label="Поиск по счетам" value={text} onChange={(e) => setText(e.target.value)} />
              </div>
              <select className="input compact" aria-label="Месяц" value={month} onChange={(e) => setS({ month: e.target.value })}>
                <option value="all">Все месяцы</option>
                {meta.data?.months.map((m) => <option key={m} value={m}>{meta.data!.month_labels[m]}</option>)}
              </select>
            </>
          )}
          <select className="input compact" aria-label="Машина" value={s.vehicle} onChange={(e) => setS({ vehicle: e.target.value })}>
            <option value="">Все машины</option>
            {vehicles.data?.items.map((v) => <option key={v.id} value={v.id}>{v.plate} — {v.name || "без названия"}</option>)}
          </select>
          {s.tab === "list" && <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={s.archived === "1"} onChange={(e) => setS({ archived: e.target.checked ? "1" : "" })} /> <span>Архив</span></label>}
        </div>
        {s.tab === "list" ? (
          <>
            <div style={{ padding: "12px 14px" }}>
              <FilterChips value={s.cat} onChange={(v) => setS({ cat: v, page: "1" })}
                options={[{ id: "", label: "Все" }, ...(meta.data?.categories.map((x) => ({ id: x.code, label: x.label })) ?? []), { id: "none", label: "Без категории" }]} />
            </div>
            {d?.budget ? (
              <div className="budget-boxes">
                {d.budget.items.map((b) => <BudgetBox key={`${d.budget!.month}-${b.code}`} b={b} month={d.budget!.month} can={d.budget!.can_edit} onSaved={refresh} />)}
                {d.budget.none > 0 && <p className="muted small budget-none">Счета без категории на {fmtMoney(d.budget.none)} ₽ в бюджет не входят: выберите им категорию в списке.</p>}
              </div>
            ) : month === "all" ? <p className="muted small" style={{ padding: "0 14px 10px" }}>Бюджет показывается, когда выбран один месяц.</p> : null}
            <QueryState q={list}>
              {d && (d.items.length === 0 ? (
                <Empty icon={ReceiptText} title={filtered ? "Ничего не найдено" : "Счетов за этот период нет"}>
                  {filtered ? <Button onClick={() => { setText(""); setS({ q: "", cat: "", vehicle: "", archived: "" }); }}>Сбросить фильтры</Button>
                    : can?.create ? "Нажмите «Загрузить счёт» и приложите файл или фото счёта: программа прочитает его сама. Или выпишите счёт здесь." : "Когда счета появятся, они будут здесь."}
                </Empty>
              ) : (
                <>
                  <Table rows={d.items} rowKey={(r) => r.id} onRowClick={(r) => nav(`/bills/${r.id}`)} dim={(r) => r.archived || r.status === "cancelled"}
                    columns={[
                      { key: "n", header: "№ счёта", render: (r) => <><strong>{r.number}</strong>{r.files > 0 && <Paperclip size={13} aria-label="есть файл" style={{ marginLeft: 6, color: "var(--text-2)" }} />}</> },
                      { key: "d", header: "Дата", render: (r) => fmtDate(r.bill_date) },
                      { key: "s", header: "Поставщик", render: (r) => r.supplier_name },
                      { key: "c", header: "Категория", render: (r) => (can?.edit && !r.archived ? (
                        <select className="input compact bill-cat" value={r.category ?? ""} aria-label={`Категория счёта ${r.number}`} onClick={(e) => e.stopPropagation()}
                          onChange={(e) => setCategory(r, e.target.value)}>
                          <option value="">—</option>
                          {meta.data?.categories.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
                        </select>
                      ) : r.category_label || <span className="muted">—</span>) },
                      { key: "v", header: "Машина", hideSm: true, render: (r) => r.vehicle || <span className="muted">—</span> },
                      { key: "r", header: "Обращение", hideSm: true, render: (r) => (r.request_id ? <Link to={`/requests/${r.request_id}`} onClick={(e) => e.stopPropagation()}>№ {r.request_number}</Link> : <span className="muted">—</span>) },
                      { key: "t", header: "Сумма, ₽", num: true, render: (r) => <strong>{fmtMoney(r.total)}</strong> },
                      { key: "st", header: "Состояние", render: (r) => (r.archived ? <Badge>в архиве</Badge> : <BillStatus code={r.status} label={r.status_label} />) },
                    ]} />
                  <Pagination page={d.page} pageSize={d.page_size} total={d.total} onPage={(p) => setS({ page: String(p) })} />
                </>
              ))}
            </QueryState>
          </>
        ) : <SummaryTab entity={entity} vehicle={s.vehicle} />}
      </Card>
      {auto && meta.data && <BillAuto meta={meta.data} entity={entity} onClose={() => setAuto(false)} />}
    </>
  );
}
