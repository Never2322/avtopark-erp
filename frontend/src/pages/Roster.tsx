// «Водители и техника»: за каждой машиной свой водитель, и сразу видно, что с ней сейчас.
// Только просмотр: закрепление водителя и смена статуса делаются в карточке техники.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Download, Search, Truck, UserX } from "lucide-react";
import { get } from "../api";
import { useAuth } from "../auth";
import { useDebounced, useEntityParam, useUrlState } from "../hooks";
import { fmtDate, fmtNum, plural } from "../format";
import { Alert, Badge, Card, Column, Empty, FilterChips, PageHeader, QueryState, Table, VehicleStatus } from "../ui";

interface Flag { code: string; label: string }
interface RosterItem {
  id: number; plate: string; name: string; label: string; legal_entity_id: number; legal_entity: string; type: string;
  status: string; status_label: string; status_since: string | null; primary_reading: string;
  current_km: number | null; current_hours: number | null; reading_age_days: number | null;
  drivers: { id: number; name: string; role: string }[];
  job: { today: number; open: number; done: number; stale: number; next: { id: number; address: string; customer: string; time_from: string; state: string; state_label: string } | null } | null;
  last_trip: { date: string; route: string } | null;
  fuel: { date: string; liters: number; per100: number | null; norm: number | null; over_pct: number | null } | null;
  open_requests: number | null;
  flags: Flag[];
}
interface RosterData {
  today: string;
  totals: { vehicles: number; no_driver: number; attention: number; by_status: { code: string; label: string; count: number }[] };
  items: RosterItem[];
  free_drivers: { id: number; name: string }[] | null;
  can: { jobs: boolean; trips: boolean; fuel: boolean; requests: boolean; export: boolean; assign: boolean; employees: boolean };
}

const FLAG_TONE: Record<string, "warning" | "error"> = { no_driver: "warning", fuel_over: "error", stale_job: "error", old_reading: "warning" };
const SHORT: Record<string, string> = { no_driver: "Нет водителя", fuel_over: "Перерасход", stale_job: "Задание не закрыто", old_reading: "Пробег устарел" };

function exportUrl(params: Record<string, string | number | undefined>) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  return `/api/roster/export.xlsx${q.toString() ? "?" + q : ""}`;
}

export function Roster() {
  const { me } = useAuth();
  const entity = useEntityParam();
  const [s, setS] = useUrlState({ q: "", status: "", only: "" });
  const [text, setText] = useState(s.q);
  const debounced = useDebounced(text);
  useEffect(() => { if (debounced !== s.q) setS({ q: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  const q = useQuery({
    queryKey: ["roster", entity, s.q, s.status, s.only],
    queryFn: () => get<RosterData>("/roster", { legal_entity_id: entity, q: s.q, status: s.status, only: s.only }),
    placeholderData: (p) => p, refetchInterval: 60_000,
  });
  const d = q.data;
  const manyEntities = (me?.legal_entities.length ?? 0) > 1 && entity === undefined;

  const cols: Column<RosterItem>[] = [
    { key: "v", header: "Машина", render: (r) => (
      <div className="roster-cell">
        <Link to={`/vehicles/${r.id}`}><strong>{r.plate}</strong></Link>
        <span className="muted small">{r.name || r.type}</span>
        {manyEntities && <span className="muted small">{r.legal_entity}</span>}
      </div>) },
    { key: "d", header: "Водитель", render: (r) => {
      const main = r.drivers.filter((x) => x.role === "primary"), spare = r.drivers.filter((x) => x.role !== "primary");
      return (
        <div className="roster-cell">
          {main.length ? main.map((x) => <strong key={x.id}>{x.name}</strong>) : <Badge tone="warning" icon={UserX}>Не назначен</Badge>}
          {spare.length > 0 && <span className="muted small">запасной: {spare.map((x) => x.name).join(", ")}</span>}
        </div>);
    } },
    { key: "s", header: "Сейчас", render: (r) => (
      <div className="roster-cell">
        <VehicleStatus code={r.status} label={r.status_label} />
        {r.status_since && <span className="muted small">с {fmtDate(r.status_since)}</span>}
      </div>) },
  ];
  if (d?.can.jobs) cols.push({ key: "j", header: "Задание сегодня", render: (r) => {
    if (!r.job) return <span className="muted">—</span>;
    const n = r.job.next;
    return (
      <div className="roster-cell wide">
        {n ? <><span>{n.time_from ? `${n.time_from} · ` : ""}{n.address}</span>
          <span className="muted small">{r.job.today > 1 ? `${r.job.done} из ${r.job.today} выполнено · ` : ""}{n.state_label}</span></> : <span className="muted">заданий нет</span>}
        {r.job.stale > 0 && <Badge tone="error">Не закрыто за прошлые дни: {r.job.stale}</Badge>}
      </div>);
  } });
  if (d?.can.trips) cols.push({ key: "t", header: "Последний рейс", hideSm: true, render: (r) => r.last_trip
    ? <div className="roster-cell wide"><span>{r.last_trip.route}</span><span className="muted small">{fmtDate(r.last_trip.date)}</span></div> : <span className="muted">—</span> });
  if (d?.can.fuel) cols.push({ key: "f", header: "Заправка и расход", hideSm: true, render: (r) => r.fuel ? (
    <div className="roster-cell">
      <span>{fmtDate(r.fuel.date)} · {fmtNum(r.fuel.liters, 1)} л</span>
      {r.fuel.per100 !== null
        ? <span className="small"><Badge tone={r.fuel.over_pct !== null ? "error" : "success"}>{fmtNum(r.fuel.per100)} л/100 км</Badge>{r.fuel.norm ? <span className="muted"> норма {fmtNum(r.fuel.norm)}</span> : null}</span>
        : <span className="muted small">расход пока не посчитан</span>}
    </div>) : <span className="muted">—</span> });
  if (d?.can.requests) cols.push({ key: "r", header: "Обращения", num: true, hideSm: true, render: (r) => r.open_requests
    ? <Link to={`/requests?status=open&q=${encodeURIComponent(r.plate)}`}><Badge tone="warning">{r.open_requests}</Badge></Link> : <span className="muted">—</span> });
  cols.push({ key: "k", header: "Пробег", num: true, hideSm: true, render: (r) => {
    const v = r.primary_reading === "km" ? r.current_km : r.current_hours;
    return v === null ? <span className="muted">—</span> : <div className="roster-cell right"><span>{fmtNum(v, 0)} {r.primary_reading === "km" ? "км" : "м·ч"}</span>{r.reading_age_days !== null && r.reading_age_days > 0 && <span className="muted small">{r.reading_age_days} дн. назад</span>}</div>;
  } });
  cols.push({ key: "w", header: "Замечания", render: (r) => r.flags.length
    ? <div className="roster-flags">{r.flags.map((f) => <Badge key={f.code} tone={FLAG_TONE[f.code] ?? "warning"}><span title={f.label}>{SHORT[f.code] ?? f.label}</span></Badge>)}</div>
    : <span className="muted">—</span> });

  const free = d?.free_drivers ?? [];
  return (
    <>
      <PageHeader title="Водители и техника" sub="За каждой машиной закреплён свой водитель. Здесь видно, кто на чём, в каком состоянии техника и что у неё сегодня"
        actions={d?.can.export ? <a className="btn" href={exportUrl({ legal_entity_id: entity, q: s.q, status: s.status, only: s.only })}><Download size={16} aria-hidden /> Скачать в Excel</a> : undefined} />
      <QueryState q={q}>
        {d && (
          <div className="stack">
            {free.length > 0 && (
              <Alert tone="warning">
                Водители без машины: <strong>{free.map((x) => x.name).join(", ")}</strong>. {d.can.assign ? "Закрепить водителя можно в карточке техники, на вкладке «Водители»." : "Закрепить водителя может диспетчер."}
              </Alert>
            )}
            <div className="toolbar">
              <div className="search-box">
                <Search size={16} aria-hidden />
                <input className="input" placeholder="Госномер, марка или фамилия водителя" aria-label="Поиск" value={text} onChange={(e) => setText(e.target.value)} />
              </div>
              <select className="input compact" aria-label="Состояние техники" value={s.status} onChange={(e) => setS({ status: e.target.value })}>
                <option value="">Любое состояние</option>
                {d.totals.by_status.filter((x) => x.count > 0 || x.code === s.status).map((x) => <option key={x.code} value={x.code}>{x.label} ({x.count})</option>)}
              </select>
            </div>
            <FilterChips value={s.only as "" | "no_driver" | "attention"} onChange={(v) => setS({ only: v })} options={[
              { id: "", label: "Вся техника", count: d.totals.vehicles },
              { id: "no_driver", label: "Без водителя", count: d.totals.no_driver },
              { id: "attention", label: "С замечаниями", count: d.totals.attention },
            ]} />
            <Card flush>
              {d.items.length === 0
                ? <Empty icon={d.totals.vehicles === 0 ? Truck : AlertTriangle} title={d.totals.vehicles === 0 ? "Техники пока нет" : "Ничего не найдено"}>
                    {d.totals.vehicles === 0 ? "Добавьте технику в разделе «Транспорт»." : "Измените поиск или отбор."}</Empty>
                : <Table columns={cols} rows={d.items} rowKey={(r) => r.id} />}
            </Card>
            <p className="muted small">
              Показано {d.items.length} из {d.totals.vehicles} {plural(d.totals.vehicles, "машины", "машин", "машин")}. Замечания: нет водителя, расход выше нормы на 30 % и больше,
              задание не закрыто за прошлые дни, пробег у машины «в работе» не обновляли больше недели.
            </p>
          </div>
        )}
      </QueryState>
    </>
  );
}
