import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BellRing, CalendarCheck, ClipboardList, Gauge, Plus, Truck, Wrench } from "lucide-react";
import { get } from "../api";
import { useAuth } from "../auth";
import { useEntityParam } from "../hooks";
import { fmtDate, fmtDateTime } from "../format";
import { Alert, Card, Empty, PageHeader, Priority, QueryState, RequestStatus, Tone } from "../ui";

interface Dash {
  date: string;
  vehicles: { total: number; attention: number; by_status: { code: string; label: string; count: number }[] } | null;
  requests: { open: number; urgent: number; overdue: number; awaiting_approval: number;
    latest: { id: number; number: number; title: string; status: string; status_label: string; priority: string; priority_label: string; plate: string }[] } | null;
  waybills: { open: number; drafts: number; today: number; forgotten: number; open_list: { id: number; number_label: string; plate: string; driver: string; opened_at: string }[] } | null;
  readings: { pending: number } | null;
  jobs: { today: number; open: number; done: number; stale: number; own: boolean } | null;
  audit: { latest: { id: number; at: string; user_login: string; action_label: string }[] } | null;
}

const STATUS_COLOR: Record<string, string> = {
  in_work: "var(--success)", in_repair: "var(--error)", awaiting_repair: "var(--warning)", parked: "#6f858d", day_off: "#6f858d",
  rented: "var(--info)", unused: "#4a626a", written_off: "#4a626a",
};

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
  return (
    <>
      <PageHeader title="Главная" sub={`Сегодня ${fmtDate(d?.date ?? new Date().toISOString().slice(0, 10))}`}
        actions={<>
          {can("requests.create") && <Link className="btn" to="/requests?new=1"><Wrench size={16} aria-hidden /> Обращение</Link>}
          {(can("waybills.create") || can("waybills.create_own")) && <Link className="btn" to="/waybills?new=1"><ClipboardList size={16} aria-hidden /> Путевой лист</Link>}
          {can("vehicles.create") && <Link className="btn primary" to="/vehicles?new=1"><Plus size={16} aria-hidden /> Техника</Link>}
        </>} />
      <QueryState q={q}>
        {d && (
          <div className="stack">
            {d.waybills && d.waybills.forgotten > 0 && (
              <Alert tone="warning">Путевых листов, открытых дольше суток: {d.waybills.forgotten}. Возможно, их забыли закрыть. <Link to="/waybills?status=open">Открыть список</Link></Alert>
            )}
            {d.jobs && d.jobs.stale > 0 && (
              <Alert tone="warning">Нарядов, не закрытых за прошлые дни: {d.jobs.stale}. Их нужно закрыть или отменить. <Link to={d.jobs.own ? "/jobs/mine" : "/jobs"}>Открыть наряды</Link></Alert>
            )}
            <div className="grid cols-4">
              {d.jobs && <Kpi to={d.jobs.own ? "/jobs/mine" : "/jobs"} icon={CalendarCheck} label="Наряды на сегодня" value={d.jobs.today}
                hint={d.jobs.today ? `Не закончено: ${d.jobs.open} · выполнено: ${d.jobs.done}` : "На сегодня нарядов нет"} tone={d.jobs.stale ? "warning" : undefined} />}
              {d.vehicles && <Kpi to="/vehicles" icon={Truck} label="Техника" value={d.vehicles.total} hint={d.vehicles.attention ? `В ремонте или ждёт: ${d.vehicles.attention}` : "Всё на ходу"} tone={d.vehicles.attention ? "warning" : undefined} />}
              {d.requests && <Kpi to="/requests?status=open" icon={Wrench} label="Открытые обращения" value={d.requests.open}
                hint={[d.requests.urgent && `срочных: ${d.requests.urgent}`, d.requests.overdue && `просрочено: ${d.requests.overdue}`, d.requests.awaiting_approval && `ждут согласования: ${d.requests.awaiting_approval}`].filter(Boolean).join(" · ") || "Срочных нет"}
                tone={d.requests.overdue ? "error" : d.requests.urgent ? "warning" : undefined} />}
              {d.waybills && <Kpi to="/waybills?status=active" icon={ClipboardList} label="Путевые листы в работе" value={d.waybills.open} hint={`Черновиков: ${d.waybills.drafts} · сегодня: ${d.waybills.today}`} />}
              {rem.data && <Kpi to="/reminders" icon={BellRing} label="Напоминания" value={rem.data.due + rem.data.soon}
                hint={rem.data.total ? `Наступило: ${rem.data.due} · скоро: ${rem.data.soon}` : "Ничего не истекает"} tone={rem.data.due ? "error" : rem.data.soon ? "warning" : undefined} />}
              {d.readings && <Kpi to="/readings" icon={Gauge} label="Показания на проверке" value={d.readings.pending} hint={d.readings.pending ? "Нужно решение" : "Очередь пуста"} tone={d.readings.pending ? "warning" : undefined} />}
            </div>
            {!d.vehicles && !d.requests && !d.waybills && !d.readings && !d.jobs && !rem.data && (
              <Empty icon={AlertTriangle} title="Для вашей роли здесь пока нечего показывать">Перейдите в нужный раздел через меню слева.</Empty>
            )}
            <div className="grid cols-2">
              {d.vehicles && (
                <Card title="Техника по статусам">
                  {d.vehicles.total === 0 ? <Empty icon={Truck} title="Техники пока нет">{can("vehicles.create") ? "Добавьте первую машину кнопкой выше." : "Попросите добавить технику."}</Empty> : (
                    <div className="bars">
                      {d.vehicles.by_status.filter((s) => s.count > 0).map((s) => (
                        <Link key={s.code} to={`/vehicles?status=${s.code}`} className="bar" style={{ color: "inherit", textDecoration: "none" }}>
                          <span>{s.label}</span>
                          <span className="track"><span className="fill" style={{ display: "block", width: `${(s.count / d.vehicles!.total) * 100}%`, background: STATUS_COLOR[s.code] }} /></span>
                          <span className="mono right">{s.count}</span>
                        </Link>
                      ))}
                    </div>
                  )}
                </Card>
              )}
              {d.waybills && (
                <Card title="Сейчас в рейсе" actions={<Link to="/waybills?status=open">Все</Link>}>
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
              {d.requests && (
                <Card title="Последние обращения" actions={<Link to="/requests?status=open">Все открытые</Link>}>
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
                <Card title="Последние действия" actions={<Link to="/audit">Весь журнал</Link>}>
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
