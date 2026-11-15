// Кабинет водителя: главная страница человека, у которого есть своя техника, но нет права смотреть весь парк.
// Сверху то, что делают чаще всего (рейс, заправка), ниже машина, наряды на сегодня и последние записи.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarCheck, ClipboardList, Fuel, Gauge, Route as RouteIcon, Truck, Wrench } from "lucide-react";
import { get } from "../api";
import { useAuth } from "../auth";
import { fmtDate, fmtDateTime, fmtNum, plural } from "../format";
import { OutboxList } from "../components/Outbox";
import { Alert, Badge, Button, Card, Empty, PageHeader, QueryState, RequestStatus, VehicleStatus } from "../ui";
import { EntryCard, EntryModal, type Mine as EntriesData } from "./Entries";
import { JobCard, useRefresh, type Mine as JobsData } from "./Jobs";

interface HomeVehicle {
  id: number; plate: string; label: string; type: string; status: string; status_label: string; status_since: string | null; primary_reading: string;
  current_km: number | null; current_hours: number | null;
  requests: { id: number; number: number; title: string; status: string; status_label: string; priority: string; priority_label: string }[];
  fuel: { date: string; liters: number; per100: number | null; norm: number | null; over_pct: number | null } | null;
}
interface Home {
  today: string; name: string; has_vehicle: boolean; vehicles: HomeVehicle[];
  can: { requests: boolean; waybills: boolean; trip: boolean; fuel: boolean; jobs: boolean; reading: boolean };
}

const longDate = (iso: string) => {
  const t = new Date(iso + "T12:00:00").toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  return t.charAt(0).toUpperCase() + t.slice(1);
};

function VehicleBlock({ v, canRequest }: { v: HomeVehicle; canRequest: boolean }) {
  const reading = v.primary_reading === "km" ? v.current_km : v.current_hours;
  return (
    <Card className="vehicle-home">
      <div className="job-head">
        <span className="entry-ico" aria-hidden><Truck size={18} /></span>
        <div className="job-title">
          <strong className="plate-big">{v.plate}</strong>
          <span className="muted small">{v.label.replace(v.plate + " · ", "") || v.type}</span>
        </div>
        <VehicleStatus code={v.status} label={v.status_label} />
      </div>
      <div className="facts">
        {reading !== null && <div><span className="muted small">{v.primary_reading === "km" ? "Пробег" : "Моточасы"}</span><strong><Gauge size={14} aria-hidden /> {fmtNum(reading, 0)} {v.primary_reading === "km" ? "км" : "м·ч"}</strong></div>}
        {v.status_since && v.status !== "in_work" && <div><span className="muted small">Состояние с</span><strong>{fmtDate(v.status_since)}</strong></div>}
        {v.fuel && <div><span className="muted small">Последняя заправка</span><strong><Fuel size={14} aria-hidden /> {fmtDate(v.fuel.date)} · {fmtNum(v.fuel.liters, 1)} л</strong></div>}
        {v.fuel?.per100 != null && <div><span className="muted small">Расход{v.fuel.norm ? ` (норма ${fmtNum(v.fuel.norm)})` : ""}</span><strong>{fmtNum(v.fuel.per100)} л/100 км {v.fuel.over_pct !== null && <Badge tone="error">выше нормы</Badge>}</strong></div>}
      </div>
      {v.status === "in_repair" && v.requests.length === 0 && canRequest && (
        <Alert tone="warning">Машина на ремонте, а обращения нет. <Link to={`/requests?new=1&vehicle=${v.id}`}>Написать, что нужно</Link></Alert>
      )}
      {v.requests.length > 0 && (
        <div className="stack-sm">
          <span className="muted small">Открытые обращения</span>
          <ul className="plain-list">
            {v.requests.map((r) => (
              <li key={r.id} className="row wrap" style={{ gap: 8 }}>
                <Link to={`/requests/${r.id}`}>№ {r.number} · {r.title}</Link>
                <RequestStatus code={r.status} label={r.status_label} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {canRequest && <div><Link className="btn sm" to={`/requests?new=1&vehicle=${v.id}`}><Wrench size={14} aria-hidden /> Написать обращение по этой машине</Link></div>}
    </Card>
  );
}

export function DriverHome() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const home = useQuery({ queryKey: ["driver-home"], queryFn: () => get<Home>("/driver/home"), refetchInterval: 60_000 });
  const entries = useQuery({ queryKey: ["entries-mine"], queryFn: () => get<EntriesData>("/entries/mine"), refetchInterval: 60_000 });
  const jobs = useQuery({ queryKey: ["jobs-mine"], queryFn: () => get<JobsData>("/jobs/mine"), refetchInterval: 60_000, enabled: home.data?.can.jobs ?? false });
  const refreshJobs = useRefresh();
  const [form, setForm] = useState<"trip" | "fuel" | null>(null);
  const h = home.data;
  const refreshEntries = () => { for (const k of ["entries-mine", "driver-home", "dashboard"]) qc.invalidateQueries({ queryKey: [k] }); };

  const todayJobs = jobs.data ? jobs.data.items.filter((j) => j.job_date <= jobs.data!.today) : [];
  const tomorrow = jobs.data ? jobs.data.items.filter((j) => j.job_date > jobs.data!.today).length : 0;
  const lastEntries = (entries.data?.items ?? []).slice(0, 3);
  const single = h && h.vehicles.length === 1 ? h.vehicles[0] : null;

  return (
    <>
      <PageHeader title={`Здравствуйте${h?.name || me?.display_name ? ", " + (h?.name || me?.display_name) : ""}`} sub={h ? longDate(h.today) : undefined} />
      <QueryState q={home}>
        {h && (
          <div className="stack">
            <OutboxList />
            {!h.has_vehicle ? (
              <Card><Empty icon={Truck} title="За вами не закреплена техника">Попросите диспетчера закрепить за вами машину: тогда здесь появятся наряды, рейсы и заправки.</Empty></Card>
            ) : (
              <>
                <div className="entry-quick">
                  <Button variant="primary" size="lg" icon={RouteIcon} disabled={!h.can.trip || !entries.data?.can.trip} onClick={() => setForm("trip")}>Рейс</Button>
                  <Button variant="primary" size="lg" icon={Fuel} disabled={!h.can.fuel || !entries.data?.can.fuel} onClick={() => setForm("fuel")}>Заправка</Button>
                  {h.can.requests && <Link className="btn lg" to={single ? `/requests?new=1&vehicle=${single.id}` : "/requests?new=1"}><Wrench size={16} aria-hidden /> Обращение</Link>}
                  {h.can.waybills && <Link className="btn lg" to="/waybills"><ClipboardList size={16} aria-hidden /> Путевые листы</Link>}
                </div>

                <div className="grid cols-2 vehicles-grid">{h.vehicles.map((v) => <VehicleBlock key={v.id} v={v} canRequest={h.can.requests} />)}</div>

                {h.can.jobs && (
                  <section className="stack-sm">
                    <h2 className="row" style={{ gap: 8 }}><CalendarCheck size={18} aria-hidden /> Наряды
                      {todayJobs.length > 0 && <Badge tone="mint">{todayJobs.length} {plural(todayJobs.length, "наряд", "наряда", "нарядов")}</Badge>}
                    </h2>
                    {jobs.isLoading ? <p className="muted">Загрузка…</p> : todayJobs.length === 0 ? (
                      <Card><p className="muted">На сегодня нарядов нет.{tomorrow > 0 ? ` На завтра: ${tomorrow}.` : ""} <Link to="/jobs/mine">Все наряды</Link></p></Card>
                    ) : (
                      <div className="job-list">{todayJobs.map((j) => <JobCard key={j.id} job={j} driver onChanged={refreshJobs} />)}</div>
                    )}
                    {todayJobs.length > 0 && tomorrow > 0 && <p className="muted small">Ещё нарядов на завтра: {tomorrow}. <Link to="/jobs/mine">Открыть все наряды</Link></p>}
                  </section>
                )}

                {lastEntries.length > 0 && (
                  <section className="stack-sm">
                    <h2 className="row" style={{ gap: 8 }}>Последние записи <Link className="small" to="/entries">Все записи</Link></h2>
                    <div className="job-list">{lastEntries.map((e) => <EntryCard key={`${e.kind}-${e.id}`} e={e} onChanged={refreshEntries} />)}</div>
                  </section>
                )}
                <p className="muted small">Обновлено {fmtDateTime(new Date(home.dataUpdatedAt).toISOString())}. Если связи нет, записи сохраняются в телефоне и уходят сами.</p>
              </>
            )}
          </div>
        )}
      </QueryState>
      {entries.data && form && <EntryModal kind={form} data={entries.data} onClose={() => setForm(null)} onDone={refreshEntries} />}
    </>
  );
}
