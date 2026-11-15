// Отчёты: список доступных и сам отчёт (период, диаграмма, таблица с итогами, дополнительные таблицы, выгрузка в Excel и CSV).
// Что показывать, решает сервер: столбцы без права доступа сюда не приходят, а в «hidden» сказано, чего не хватает.
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BarChart3, ChevronLeft, ChevronRight, FileSpreadsheet, FileText } from "lucide-react";
import { get, qs, ApiError } from "../api";
import { fmtDate, fmtDateTime, fmtMoney, fmtNum, todayIso } from "../format";
import { useEntityParam, useUrlState } from "../hooks";
import { Alert, Button, Card, Empty, PageHeader, QueryState } from "../ui";

interface ReportItem { name: string; title: string; period: "month" | "range" | "year12"; money: boolean; desc: string }
interface Cell { key: string; label: string; kind: string }
type Value = string | number | null;
interface Tbl { columns: Cell[]; rows: Value[][]; totals: Value[] | null }
interface Extra extends Tbl { title: string }
interface ChartData { labels: string[]; values: number[]; unit: string; titles: string[] }
interface ReportData {
  name: string; title: string; note: string; money: boolean; hidden: string[]; legal_entity_id: number | null;
  period: { kind: "month" | "range" | "year12"; from: string; to: string; month: string };
  table: Tbl; chart: ChartData | null; extras: Extra[]; can: { export: boolean };
}

/* ---------------- даты ---------------- */

const pad = (n: number) => String(n).padStart(2, "0");
const isoOf = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`;
const lastDay = (y: number, m: number) => new Date(Date.UTC(y, m, 0)).getUTCDate();

function shiftMonth(month: string, delta: number): string {
  const y = Number(month.slice(0, 4)), m = Number(month.slice(5, 7)) - 1 + delta;
  return `${y + Math.floor(m / 12)}-${pad(((m % 12) + 12) % 12 + 1)}`;
}

function presets(today: string): { label: string; from: string; to: string }[] {
  const y = Number(today.slice(0, 4)), m = Number(today.slice(5, 7));
  const prev = shiftMonth(today.slice(0, 7), -1), py = Number(prev.slice(0, 4)), pm = Number(prev.slice(5, 7));
  const d30 = new Date(Date.UTC(y, m - 1, Number(today.slice(8, 10))) - 29 * 86400000);
  return [
    { label: "Этот месяц", from: isoOf(y, m, 1), to: today },
    { label: "Прошлый месяц", from: isoOf(py, pm, 1), to: isoOf(py, pm, lastDay(py, pm)) },
    { label: "Последние 30 дней", from: isoOf(d30.getUTCFullYear(), d30.getUTCMonth() + 1, d30.getUTCDate()), to: today },
    { label: "С начала года", from: isoOf(y, 1, 1), to: today },
    { label: "Прошлый год", from: isoOf(y - 1, 1, 1), to: isoOf(y - 1, 12, 31) },
  ];
}

/* ---------------- значения ---------------- */

function show(kind: string, key: string, v: Value, total = false): string {
  // в итоговой строке первой колонкой может стоять подпись «Итого», даже если колонка датная
  if (total && typeof v === "string") return v;
  if (v === null || v === undefined || v === "") {
    if (kind === "datetime") return key === "from" ? "до начала учёта" : key === "to" ? "идёт сейчас" : "—";
    return "—";
  }
  if (typeof v === "string") return kind === "date" ? fmtDate(v) : kind === "datetime" ? fmtDateTime(v) : v;
  if (kind === "money") return v === 0 && !total ? "—" : fmtMoney(v);
  if (kind === "dec") return v === 0 && !total ? "—" : fmtNum(v, 1);
  if (kind === "int") return fmtNum(v, 0);
  return String(v);
}

const isNumeric = (kind: string) => kind === "money" || kind === "dec" || kind === "int";

/* ---------------- диаграмма ---------------- */

function niceMax(peak: number): number {
  if (peak <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(peak)));
  for (const k of [1, 2, 2.5, 5, 10]) if (k * exp >= peak) return k * exp;
  return 10 * exp;
}

function BarChart({ chart }: { chart: ChartData }) {
  const W = 760, H = 262, L = 56, R = 12, T = 28, B = 66;
  const max = niceMax(Math.max(...chart.values, 0));
  const n = chart.values.length, step = (W - L - R) / n, bw = Math.min(40, step * 0.62);
  const y = (v: number) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((k) => k * max);
  const labelled = n <= 14;
  return (
    <svg className="report-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Диаграмма, ${chart.unit}`}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} />
          <text x={L - 8} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--text-2)">{fmtNum(t, 0)}</text>
        </g>
      ))}
      <text x={L} y={12} textAnchor="start" fontSize={11} fill="var(--text-2)">{chart.unit}</text>
      {chart.values.map((v, i) => {
        const x = L + step * i + (step - bw) / 2, top = y(v);
        return (
          <g key={i}>
            <rect x={x} y={top} width={bw} height={Math.max(H - B - top, v > 0 ? 1 : 0)} rx={3} fill="var(--mint)" opacity={0.9}>
              <title>{`${chart.titles[i] ?? chart.labels[i]}: ${fmtNum(v, 1)} ${chart.unit}`}</title>
            </rect>
            {labelled && v > 0 && <text x={x + bw / 2} y={top - 4} textAnchor="middle" fontSize={10} fill="var(--text)">{fmtNum(v, 0)}</text>}
            <text x={x + bw / 2} y={H - B + 14} fontSize={11} fill="var(--text-2)" textAnchor="end" transform={`rotate(-35 ${x + bw / 2} ${H - B + 14})`}>{chart.labels[i]}</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ---------------- таблица ---------------- */

function DataTable({ t, empty }: { t: Tbl; empty: string }) {
  if (t.rows.length === 0) return <Empty icon={BarChart3} title="Нет данных">{empty}</Empty>;
  return (
    <div className="table-wrap">
      <table className="table report-table">
        <thead>
          <tr>{t.columns.map((c) => <th key={c.key} className={isNumeric(c.kind) ? "num" : ""}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {t.rows.map((r, i) => (
            <tr key={i}>
              {t.columns.map((c, j) => <td key={c.key} className={isNumeric(c.kind) ? "num" : ""}>{show(c.kind, c.key, r[j])}</td>)}
            </tr>
          ))}
        </tbody>
        {t.totals && (
          <tfoot>
            <tr>
              {t.columns.map((c, j) => {
                const v = t.totals![j];
                return <td key={c.key} className={isNumeric(c.kind) ? "num" : ""}><strong>{v === null || v === undefined ? "" : show(c.kind, c.key, v, true)}</strong></td>;
              })}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

/* ---------------- список отчётов ---------------- */

const PERIOD_TEXT = { month: "За месяц", range: "За выбранные даты", year12: "12 месяцев" };

export function ReportsIndex() {
  const q = useQuery({ queryKey: ["reports"], queryFn: () => get<{ items: ReportItem[]; can: { export: boolean; money: boolean } }>("/reports") });
  const d = q.data;
  return (
    <>
      <PageHeader title="Отчёты" sub="Пробег, топливо, затраты, простои и заказы. Каждый отчёт можно скачать в Excel. Показывается только то, к чему у вас есть доступ" />
      <QueryState q={q}>
        {d && (d.items.length === 0
          ? <Empty icon={BarChart3} title="Отчётов для вас пока нет">Отчёты строятся по данным из разделов. Обратитесь к руководителю, если нужен доступ.</Empty>
          : (
            <div className="grid cols-3 report-cards">
              {d.items.map((r) => (
                <Link key={r.name} to={`/reports/${r.name}`} className="card report-card">
                  <div className="row"><BarChart3 size={18} aria-hidden /><h2>{r.title}</h2></div>
                  <p className="muted">{r.desc}</p>
                  <span className="muted small">{PERIOD_TEXT[r.period]}{r.money ? " · с деньгами" : ""}</span>
                </Link>
              ))}
            </div>
          ))}
        {d && !d.can.money && d.items.length > 0 && <p className="muted small" style={{ marginTop: 14 }}>Суммы в рублях в отчётах скрыты: у вас нет права видеть деньги в отчётах.</p>}
      </QueryState>
    </>
  );
}

/* ---------------- отчёт ---------------- */

export function ReportView() {
  const { name = "" } = useParams();
  const entity = useEntityParam();
  const today = todayIso();
  const [s, setS] = useUrlState({ month: "", from: "", to: "" });
  const q = useQuery({
    queryKey: ["report", name, entity, s.month, s.from, s.to],
    queryFn: () => get<ReportData>(`/reports/${name}`, { legal_entity_id: entity, month: s.month, from: s.from, to: s.to }),
    placeholderData: (p) => (p && p.name === name ? p : undefined), retry: false,
  });
  const list = useQuery({ queryKey: ["reports"], queryFn: () => get<{ items: ReportItem[] }>("/reports") });
  const d = q.data;
  const kind = d?.period.kind ?? list.data?.items.find((r) => r.name === name)?.period ?? "month";
  const month = s.month || d?.period.month || today.slice(0, 7);
  const from = s.from || d?.period.from || `${today.slice(0, 4)}-01-01`;
  const to = s.to || d?.period.to || today;
  const exportParams = d ? { legal_entity_id: entity, ...(d.period.kind === "range" ? { from: d.period.from, to: d.period.to } : { month: d.period.month }) } : {};
  const badPeriod = q.error instanceof ApiError && q.error.code === "bad_period";

  return (
    <>
      <PageHeader crumb={<Link to="/reports">Отчёты</Link>} title={d?.title ?? "Отчёт"} sub={d?.note}
        actions={d?.can.export ? (
          <div className="row wrap">
            <a className="btn" href={`/api/reports/${name}/export.xlsx${qs(exportParams)}`}><FileSpreadsheet size={16} aria-hidden /> Скачать в Excel</a>
            <a className="btn" href={`/api/reports/${name}/export.csv${qs(exportParams)}`}><FileText size={16} aria-hidden /> CSV</a>
          </div>) : undefined} />
      <div className="stack">
        <div className="toolbar report-period">
          {kind === "range" ? (
            <>
              <label className="row" style={{ gap: 6 }}><span className="muted small">С</span>
                <input className="input compact" type="date" aria-label="Дата с" value={from} onChange={(e) => setS({ from: e.target.value, to })} /></label>
              <label className="row" style={{ gap: 6 }}><span className="muted small">По</span>
                <input className="input compact" type="date" aria-label="Дата по" value={to} onChange={(e) => setS({ from, to: e.target.value })} /></label>
              <div className="chips" role="group" aria-label="Быстрый выбор периода">
                {presets(today).map((p) => <button key={p.label} className="chip-btn" aria-pressed={from === p.from && to === p.to} onClick={() => setS({ from: p.from, to: p.to })}>{p.label}</button>)}
              </div>
            </>
          ) : (
            <>
              <Button size="sm" icon={ChevronLeft} aria-label="Предыдущий месяц" onClick={() => setS({ month: shiftMonth(month, -1) })} />
              <label className="row" style={{ gap: 6 }}><span className="muted small">{kind === "year12" ? "Последний месяц" : "Месяц"}</span>
                <input className="input compact" type="month" aria-label="Месяц" value={month} onChange={(e) => e.target.value && setS({ month: e.target.value })} /></label>
              <Button size="sm" icon={ChevronRight} aria-label="Следующий месяц" onClick={() => setS({ month: shiftMonth(month, 1) })} />
              <Button size="sm" onClick={() => setS({ month: today.slice(0, 7) })}>Текущий месяц</Button>
            </>
          )}
        </div>
        {badPeriod && <Alert tone="error">{(q.error as ApiError).message}</Alert>}
        {!badPeriod && (
          <QueryState q={q} what="Отчёт">
            {d && (
              <>
                {d.hidden.length > 0 && (
                  <Alert tone="info">
                    <strong>Часть данных не показана.</strong>
                    <ul className="plain-list">{d.hidden.map((h) => <li key={h}>{h}</li>)}</ul>
                  </Alert>
                )}
                {d.chart && <Card title="Диаграмма"><BarChart chart={d.chart} /></Card>}
                <Card flush><DataTable t={d.table} empty="За выбранный период по доступным вам данным ничего нет. Измените период или юридическое лицо." /></Card>
                {d.extras.map((e) => (
                  <Card key={e.title} title={e.title} flush><DataTable t={e} empty="Нет записей" /></Card>
                ))}
                <p className="muted small">Данные обновляются при каждом открытии. Записи в архиве в отчёты не попадают.</p>
              </>
            )}
          </QueryState>
        )}
      </div>
    </>
  );
}
