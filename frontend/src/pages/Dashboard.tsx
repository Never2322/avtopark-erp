import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowDown, ArrowUp, BellRing, CalendarCheck, CalendarClock, CheckCircle2, ClipboardCheck, ClipboardList, Gauge, History, PackageSearch, PieChart, Plus, Truck, Wallet, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { get } from "../api";
import { useAuth } from "../auth";
import { useEntityParam } from "../hooks";
import { fmtDateTime, fmtNum, plural } from "../format";
import { Alert, Card, Empty, Priority, QueryState, RequestStatus, Tone } from "../ui";

interface Dash {
  date: string;
  vehicles: { total: number; attention: number; by_status: { code: string; label: string; count: number }[] } | null;
  requests: { open: number; urgent: number; overdue: number; awaiting_approval: number;
    latest: { id: number; number: number; title: string; status: string; status_label: string; priority: string; priority_label: string; plate: string }[] } | null;
  waybills: { open: number; drafts: number; today: number; forgotten: number; open_list: { id: number; number_label: string; plate: string; driver: string; opened_at: string }[] } | null;
  readings: { pending: number } | null;
  jobs: { today: number; open: number; done: number; stale: number; own: boolean } | null;
  audit: { latest: { id: number; at: string; user_login: string; action_label: string }[] } | null;
  expenses: { months: { label: string; fuel: number; repair: number; other: number; total: number }[]; month_total: number; prev_total: number; delta_pct: number | null } | null;
  maintenance: { soon: number; items: { id: number; vehicle_id: number; plate: string; name: string; kind_label: string; work: string; level: string; left_text: string; due_date: string | null }[] } | null;
  orders: { active: number; days: number[]; week: number; prev_week: number } | null;
  reminders: { total: number; due: number; items: { source_label: string; level: string; tone: string; left_text: string; title: string; subject: string; url: string }[] } | null;
}

const STATUS_COLOR: Record<string, string> = {
  in_work: "var(--success)", in_repair: "var(--error)",
};
const SERIES = [
  { key: "fuel" as const, label: "Топливо", color: "var(--mint)" },
  { key: "repair" as const, label: "Ремонт", color: "var(--info)" },
  { key: "other" as const, label: "Прочее", color: "#a78bfa" },
];

const rub = (v: number) => `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(Math.round(v))}\u00a0₽`;
const compact = (v: number) => (v >= 1_000_000 ? `${fmtNum(v / 1_000_000, 1)} млн` : v >= 1000 ? `${fmtNum(v / 1000, 0)} тыс` : String(Math.round(v)));

/** Верхняя граница оси: 1, 2, 2.5, 5 или 10 на нужный порядок, чтобы подписи были круглыми. */
function niceMax(v: number): number {
  if (v <= 0) return 1000;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}

function greeting(hour: number): string {
  return hour < 5 ? "Доброй ночи" : hour < 12 ? "Доброе утро" : hour < 18 ? "Добрый день" : "Добрый вечер";
}

function Stat({ to, icon: Icon, tone, value, label, bar, foot }: { to?: string; icon: LucideIcon; tone: "mint" | "success" | "error" | "warning" | "info"; value: string; label: string; bar?: number | null; foot?: ReactNode }) {
  const body = (
    <>
      <span className={`stat-icon ${tone}`}><Icon size={26} aria-hidden /></span>
      <span className="stat-body">
        <span className="stat-value">{value}</span>
        <span className="stat-label">{label}</span>
        {bar !== undefined && bar !== null && (
          <span className="stat-bar" aria-hidden><span className={`fill ${tone}`} style={{ width: `${Math.max(2, Math.min(100, bar))}%` }} /></span>
        )}
        {foot && <span className="stat-foot">{foot}</span>}
      </span>
    </>
  );
  return to ? <Link to={to} className="card stat">{body}</Link> : <div className="card stat">{body}</div>;
}

/** Линии по месяцам. Значения лежат в <title> точек и в скрытой таблице для чтения с экрана. */
function ExpensesChart({ months }: { months: NonNullable<Dash["expenses"]>["months"] }) {
  const W = 620, H = 230, L = 52, R = 14, T = 14, B = 28;
  const top = niceMax(Math.max(...months.flatMap((m) => SERIES.map((s) => m[s.key])), 1));
  const x = (i: number) => L + (i * (W - L - R)) / Math.max(1, months.length - 1);
  const y = (v: number) => T + (H - T - B) * (1 - v / top);
  const path = (key: "fuel" | "repair" | "other") => {
    const pts = months.map((m, i) => [x(i), y(m[key])] as const);
    // плавная кривая: контрольные точки на одной высоте с концами отрезка, поэтому линия не уходит за пределы значений
    return pts.map(([px, py], i) => {
      if (i === 0) return `M${px},${py}`;
      const [qx, qy] = pts[i - 1];
      const cx = (px - qx) / 2;
      return `C${qx + cx},${qy} ${px - cx},${py} ${px},${py}`;
    }).join(" ");
  };
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * top);
  return (
    <div className="chart-wrap">
      <div className="chart-scroll">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Затраты по месяцам: топливо, ремонт, прочее" className="chart">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid-line" />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" className="axis">{t === 0 ? "0 ₽" : compact(t)}</text>
          </g>
        ))}
        {months.map((m, i) => <text key={i} x={x(i)} y={H - 8} textAnchor="middle" className="axis">{m.label}</text>)}
        {SERIES.map((s) => (
          <g key={s.key}>
            <path d={path(s.key)} fill="none" stroke={s.color} strokeWidth={2.5} strokeLinecap="round" />
            {months.map((m, i) => (
              <circle key={i} cx={x(i)} cy={y(m[s.key])} r={4} fill="var(--card)" stroke={s.color} strokeWidth={2.5}><title>{`${s.label}, ${m.label}: ${rub(m[s.key])}`}</title></circle>
            ))}
          </g>
        ))}
      </svg>
      </div>
      <ul className="legend">
        {SERIES.map((s) => <li key={s.key}><span className="dot" style={{ background: s.color }} aria-hidden />{s.label}</li>)}
      </ul>
      <table className="sr-only">
        <caption>Затраты по месяцам, ₽</caption>
        <thead><tr><th>Месяц</th>{SERIES.map((s) => <th key={s.key}>{s.label}</th>)}</tr></thead>
        <tbody>{months.map((m) => <tr key={m.label}><td>{m.label}</td>{SERIES.map((s) => <td key={s.key}>{rub(m[s.key])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function Donut({ parts, total, centre, sub }: { parts: { code: string; label: string; count: number }[]; total: number; centre: string; sub: string }) {
  const R = 52, C = 2 * Math.PI * R;
  let acc = 0;
  return (
    <svg viewBox="0 0 140 140" className="donut" role="img" aria-label={`${centre} ${sub}`}>
      <circle cx="70" cy="70" r={R} fill="none" stroke="var(--field)" strokeWidth="16" />
      {parts.filter((s) => s.count > 0).map((s) => {
        const len = (s.count / total) * C;
        const el = <circle key={s.code} cx="70" cy="70" r={R} fill="none" stroke={STATUS_COLOR[s.code] ?? "#4a626a"} strokeWidth="16" strokeDasharray={`${Math.max(0, len - 2)} ${C}`} strokeDashoffset={-acc} transform="rotate(-90 70 70)"><title>{`${s.label}: ${s.count}`}</title></circle>;
        acc += len;
        return el;
      })}
      <text x="70" y="68" textAnchor="middle" className="donut-num">{centre}</text>
      <text x="70" y="88" textAnchor="middle" className="donut-sub">{sub}</text>
    </svg>
  );
}

function Spark({ values }: { values: number[] }) {
  const W = 220, H = 70, top = Math.max(...values, 1);
  const pts = values.map((v, i) => [(i * W) / Math.max(1, values.length - 1), H - 6 - (v / top) * (H - 14)] as const);
  const line = pts.map(([px, py], i) => {
    if (i === 0) return `M${px},${py}`;
    const [qx, qy] = pts[i - 1];
    const cx = (px - qx) / 2;
    return `C${qx + cx},${qy} ${px - cx},${py} ${px},${py}`;
  }).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="spark" aria-hidden preserveAspectRatio="none">
      <defs><linearGradient id="spark-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="var(--success)" stopOpacity="0.35" /><stop offset="1" stopColor="var(--success)" stopOpacity="0" /></linearGradient></defs>
      <path d={`${line} L${W},${H} L0,${H} Z`} fill="url(#spark-fill)" />
      <path d={line} fill="none" stroke="var(--success)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Kpi({ to, label, value, hint, icon: Icon, tone }: { to: string; label: string; value: number; hint?: string; icon: typeof Truck; tone?: Tone }) {
  return (
    <Link to={to} className={`card kpi ${tone === "warning" ? "warn" : tone === "error" ? "bad" : ""}`}>
      <span className="label"><Icon size={16} aria-hidden /> {label}</span>
      <span className="value">{value}</span>
      {hint && <span className="hint">{hint}</span>}
    </Link>
  );
}

export function Dashboard() {
  const { can, me } = useAuth();
  const entity = useEntityParam();
  const q = useQuery({ queryKey: ["dashboard", entity], queryFn: () => get<Dash>("/dashboard", { legal_entity_id: entity }) });
  const d = q.data;
  const rem = useQuery({ queryKey: ["reminders-summary", entity], queryFn: () => get<{ due: number; soon: number; total: number }>("/reminders/summary", { legal_entity_id: entity }), enabled: can("reminders.view") });
  const tz = me?.org_timezone || "Asia/Novosibirsk";
  const now = new Date();
  const hour = Number(new Intl.DateTimeFormat("ru-RU", { timeZone: tz, hour: "numeric", hourCycle: "h23" }).format(now));
  const longDate = new Intl.DateTimeFormat("ru-RU", { timeZone: tz, day: "numeric", month: "long", year: "numeric" }).format(now);
  const weekday = new Intl.DateTimeFormat("ru-RU", { timeZone: tz, weekday: "long" }).format(now);
  const words = (me?.employee_name || me?.display_name || "").split(" ").filter(Boolean);
  // в карточке сотрудника «Фамилия Имя Отчество»: имя — второе слово; если слово одно (например, логин), берём его
  const name = words.length >= 2 ? words[1] : words[0] ?? "";
  const inWork = d?.vehicles?.by_status.find((s) => s.code === "in_work")?.count ?? 0;
  const total = d?.vehicles?.total ?? 0;
  const pct = (n: number) => (total ? Math.round((n / total) * 100) : 0);
  const exp = d?.expenses;
  const tasksTotal = d?.reminders?.total ?? (rem.data ? rem.data.due + rem.data.soon : null);
  const actions = [
    can("vehicles.create") && { to: "/vehicles?new=1", icon: Truck, label: "Добавить технику" },
    can("requests.create") && { to: "/requests?new=1", icon: Wrench, label: "Обращение на ремонт" },
    (can("waybills.create") || can("waybills.create_own")) && { to: "/waybills?new=1", icon: ClipboardList, label: "Путевой лист" },
    can("orders.view") && { to: "/orders", icon: PackageSearch, label: "Заказы" },
  ].filter(Boolean) as { to: string; icon: LucideIcon; label: string }[];

  return (
    <>
      <div className="dash-head">
        <div>
          <h1>{greeting(hour)}{name ? `, ${name}` : ""}!</h1>
          <div className="muted">Панель управления автопарком компании</div>
        </div>
        <div className="dash-date" aria-label="Сегодня">
          <CalendarClock size={20} aria-hidden />
          <span><strong>{longDate}</strong><br /><span className="muted">{weekday[0].toUpperCase() + weekday.slice(1)}</span></span>
        </div>
      </div>
      <QueryState q={q}>
        {d && (
          <div className="stack">
            {d.waybills && d.waybills.forgotten > 0 && (
              <Alert tone="warning">Путевых листов, открытых дольше суток: {d.waybills.forgotten}. Возможно, их забыли закрыть. <Link to="/waybills?status=open">Открыть список</Link></Alert>
            )}
            {d.jobs && d.jobs.stale > 0 && (
              <Alert tone="warning">Заданий, не закрытых за прошлые дни: {d.jobs.stale}. Их нужно закрыть или отменить. <Link to={d.jobs.own ? "/jobs/mine" : "/jobs"}>Открыть задания</Link></Alert>
            )}

            <div className="stat-row">
              {d.vehicles && <Stat to="/vehicles" icon={Truck} tone="mint" value={String(total)} label="Всего техники" />}
              {d.vehicles && total > 0 && <Stat to="/vehicles?status=in_work" icon={CheckCircle2} tone="success" value={String(inWork)} label="На линии" bar={pct(inWork)} foot={`${pct(inWork)}%`} />}
              {d.vehicles && total > 0 && <Stat to="/vehicles?status=in_repair" icon={Wrench} tone="error" value={String(d.vehicles.attention)} label="В ремонте" bar={pct(d.vehicles.attention)} foot={`${pct(d.vehicles.attention)}%`} />}
              {d.maintenance && <Stat to="/reminders" icon={CalendarCheck} tone="warning" value={String(d.maintenance.soon)} label="ТО скоро" />}
              {exp && (
                <Stat to="/expenses" icon={Wallet} tone="info" value={exp.month_total >= 1_000_000 ? `${fmtNum(exp.month_total / 1_000_000, 1)}\u00a0млн\u00a0₽` : rub(exp.month_total)} label="Расходы за месяц"
                  foot={exp.delta_pct === null ? "в прошлом месяце не было" : (
                    <span className={exp.delta_pct > 0 ? "bad" : "good"}>{exp.delta_pct > 0 ? <ArrowUp size={13} aria-hidden /> : <ArrowDown size={13} aria-hidden />} {exp.delta_pct > 0 ? "+" : ""}{exp.delta_pct}% к прошлому месяцу</span>
                  )} />
              )}
              {tasksTotal !== null && <Stat to="/reminders" icon={ClipboardCheck} tone="mint" value={String(tasksTotal)} label={plural(tasksTotal, "Открытая задача", "Открытые задачи", "Открытых задач")} />}
            </div>

            <div className="grid cols-4">
              {d.jobs && <Kpi to={d.jobs.own ? "/jobs/mine" : "/jobs"} icon={CalendarCheck} label="Задания на сегодня" value={d.jobs.today}
                hint={d.jobs.today ? `Не закончено: ${d.jobs.open} · выполнено: ${d.jobs.done}` : "На сегодня заданий нет"} tone={d.jobs.stale ? "warning" : undefined} />}
              {d.requests && <Kpi to="/requests?status=open" icon={Wrench} label="Открытые обращения" value={d.requests.open}
                hint={[d.requests.urgent && `срочных: ${d.requests.urgent}`, d.requests.overdue && `просрочено: ${d.requests.overdue}`, d.requests.awaiting_approval && `ждут согласования: ${d.requests.awaiting_approval}`].filter(Boolean).join(" · ") || "Срочных нет"}
                tone={d.requests.overdue ? "error" : d.requests.urgent ? "warning" : undefined} />}
              {d.waybills && <Kpi to="/waybills?status=active" icon={ClipboardList} label="Путевые листы в работе" value={d.waybills.open} hint={`Черновиков: ${d.waybills.drafts} · сегодня: ${d.waybills.today}`} />}
              {d.readings && <Kpi to="/readings" icon={Gauge} label="Показания на проверке" value={d.readings.pending} hint={d.readings.pending ? "Нужно решение" : "Очередь пуста"} tone={d.readings.pending ? "warning" : undefined} />}
              {rem.data && !d.reminders && <Kpi to="/reminders" icon={BellRing} label="Напоминания" value={rem.data.due + rem.data.soon}
                hint={rem.data.total ? `Наступило: ${rem.data.due} · скоро: ${rem.data.soon}` : "Ничего не истекает"} tone={rem.data.due ? "error" : rem.data.soon ? "warning" : undefined} />}
            </div>
            {!d.vehicles && !d.requests && !d.waybills && !d.readings && !d.jobs && !rem.data && (
              <Empty icon={AlertTriangle} title="Для вашей роли здесь пока нечего показывать">Перейдите в нужный раздел через меню слева.</Empty>
            )}

            {(exp || d.waybills) && (
              <div className={exp && d.waybills ? "dash-2-1" : "grid"}>
                {exp && (
                  <Card title="Расходы компании" icon={Wallet} actions={<span className="muted small">за 6 месяцев · <Link to="/expenses">подробнее</Link></span>}>
                    {exp.months.every((m) => m.total === 0)
                      ? <Empty icon={Wallet} title="Расходов за полгода пока нет">Они появятся после заправок, счетов на ремонт и штрафов.</Empty>
                      : <ExpensesChart months={exp.months} />}
                  </Card>
                )}
                {d.waybills && (
                  <Card title="Сейчас в рейсе" icon={ClipboardList} actions={<Link to="/waybills?status=open">Все</Link>}>
                    {d.waybills.open_list.length === 0 ? <Empty icon={ClipboardList} title="Открытых путевых листов нет" /> : (
                      <div className="stack-sm">
                        {d.waybills.open_list.map((w) => (
                          <Link key={w.id} to={`/waybills/${w.id}`} className="row" style={{ color: "inherit", textDecoration: "none", justifyContent: "space-between" }}>
                            <span><strong>{w.plate}</strong> <span className="muted">— {w.driver}</span></span>
                            <span className="muted small">с {fmtDateTime(w.opened_at)}</span>
                          </Link>
                        ))}
                      </div>
                    )}
                  </Card>
                )}
              </div>
            )}

            <div className="dash-4">
              {d.vehicles && (
                <Card title="Состояние парка" icon={PieChart}>
                  {total === 0 ? <Empty icon={Truck} title="Техники пока нет">{can("vehicles.create") ? "Добавьте первую машину кнопкой «Добавить технику»." : "Попросите добавить технику."}</Empty> : (
                    <div className="fleet">
                      <Donut parts={d.vehicles.by_status} total={total} centre={`${pct(inWork)}%`} sub="в работе" />
                      <ul className="fleet-list">
                        {d.vehicles.by_status.filter((s) => s.count > 0).map((s) => (
                          <li key={s.code}>
                            <Link to={`/vehicles?status=${s.code}`}><span className="dot" style={{ background: STATUS_COLOR[s.code] ?? "#4a626a" }} aria-hidden />{s.label}<span className="mono right">{s.count}</span></Link>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </Card>
              )}
              {d.maintenance && (
                <Card title="Ближайшее ТО" icon={Wrench} actions={<Link to="/reminders">Все</Link>}>
                  {d.maintenance.items.length === 0 ? <Empty icon={CalendarCheck} title="Плановых ТО нет">Срок ТО задаётся в карточке машины, на вкладке «ТО».</Empty> : (
                    <div className="stack-sm">
                      {d.maintenance.items.map((m) => (
                        <Link key={m.id} to={`/vehicles/${m.vehicle_id}?tab=maintenance`} className="to-item" style={{ color: "inherit", textDecoration: "none" }}>
                          <span className="to-name"><strong>{m.plate}</strong> <span className="muted">{m.name}</span></span>
                          <span className="muted small">{m.kind_label}{m.work ? `: ${m.work}` : ""}</span>
                          <span className={`badge ${m.level === "due" ? "error" : m.level === "soon" ? "warning" : "neutral"}`}>{m.level === "due" ? "Пора · " : m.level === "soon" ? "Скоро · " : ""}{m.left_text}</span>
                        </Link>
                      ))}
                    </div>
                  )}
                </Card>
              )}
              {d.orders && (
                <Card title="Заказы" icon={PackageSearch} actions={<Link to="/orders">Все заказы</Link>}>
                  <div className="orders">
                    <div><span className="orders-num">{d.orders.active}</span><span className="muted"> {plural(d.orders.active, "активный заказ", "активных заказа", "активных заказов")}</span></div>
                    <Spark values={d.orders.days} />
                    <div className="muted small">Новых за неделю: {d.orders.week}{d.orders.week !== d.orders.prev_week && ` (${d.orders.week > d.orders.prev_week ? "+" : "−"}${Math.abs(d.orders.week - d.orders.prev_week)} к прошлой)`}</div>
                  </div>
                </Card>
              )}
              {actions.length > 0 && (
                <Card title="Быстрые действия" icon={Plus}>
                  <div className="quick">
                    {actions.map((a) => <Link key={a.to} to={a.to} className="quick-btn"><a.icon size={22} aria-hidden /><span>{a.label}</span></Link>)}
                  </div>
                </Card>
              )}
            </div>

            <div className="dash-3">
              {d.reminders && (
                <Card title="Мои задачи" icon={ClipboardCheck} actions={<Link to="/reminders">Все задачи</Link>}>
                  {d.reminders.items.length === 0 ? <Empty icon={CheckCircle2} title="Срочного нет">Сроки, штрафы и ТО, которые подходят, появятся здесь.</Empty> : (
                    <div className="stack-sm">
                      {d.reminders.items.map((r, i) => (
                        <Link key={i} to={r.url} className="task" style={{ color: "inherit", textDecoration: "none" }}>
                          <span className="task-main"><span>{r.title}</span>{r.subject && <span className="muted small"> · {r.subject}</span>}</span>
                          <span className={`badge ${r.level === "due" ? "error" : "warning"}`}>{r.left_text}</span>
                        </Link>
                      ))}
                    </div>
                  )}
                </Card>
              )}
              {d.requests && (
                <Card title="Последние обращения" icon={Wrench} actions={<Link to="/requests?status=open">Все открытые</Link>}>
                  {d.requests.latest.length === 0 ? <Empty icon={Wrench} title="Открытых обращений нет" /> : (
                    <div className="stack-sm">
                      {d.requests.latest.map((r) => (
                        <Link key={r.id} to={`/requests/${r.id}`} className="row" style={{ color: "inherit", textDecoration: "none" }}>
                          <span className="muted mono">№{r.number}</span>
                          <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.title} <span className="muted">· {r.plate}</span></span>
                          <Priority code={r.priority} label={r.priority_label} />
                          <RequestStatus code={r.status} label={r.status_label} />
                        </Link>
                      ))}
                    </div>
                  )}
                </Card>
              )}
              {d.audit && (
                <Card title="Последняя активность" icon={History} actions={<Link to="/audit">Весь журнал</Link>}>
                  <div className="stack-sm">
                    {d.audit.latest.map((a) => (
                      <div key={a.id} className="row"><span className="muted small nowrap">{fmtDateTime(a.at)}</span><span>{a.action_label}</span><span className="spacer" /><span className="muted small">{a.user_login}</span></div>
                    ))}
                  </div>
                </Card>
              )}
            </div>
            {me && !me.employee_id && me.roles.some((r) => r.code === "driver") && (
              <Alert tone="warning">К вашему входу не привязана карточка сотрудника, поэтому закреплённая техника и путевые листы не видны. Попросите администратора привязать карточку.</Alert>
            )}
          </div>
        )}
      </QueryState>
    </>
  );
}
