// «Маршруты по дням»: какой мусоровоз на каком маршруте в выбранный день (по умолчанию сегодня).
// Для директора и диспетчера. Сами маршруты и расписание правятся в разделах «Маршруты» и «Расписание маршрутов».
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarRange, ChevronLeft, ChevronRight, Signpost, Truck, UserRound } from "lucide-react";
import { get } from "../api";
import { useAuth } from "../auth";
import { plural } from "../format";
import { useEntityParam, useUrlState } from "../hooks";
import { Alert, Badge, Button, Card, Empty, PageHeader, QueryState } from "../ui";
import { longDay, shiftDay, Streets } from "./routeParts";

interface TruckRow { plan_id: number; vehicle_id: number; plate: string; vehicle: string; status: string; status_name: string; driver_id: number | null; driver: string | null; source: "weekly" | "date"; note: string }
interface RouteRow { route_id: number; route: string; streets: string[]; note: string; trucks: TruckRow[] }
interface Board {
  today: string; date: string; weekday: string; routes: RouteRow[];
  routes_without_truck: { route_id: number; route: string; streets: string[] }[];
  trucks_without_route: { vehicle_id: number; plate: string; vehicle: string; status: string; status_name: string; driver: string | null }[];
  totals: { routes: number; trucks: number; routes_all: number };
}

function TruckLine({ t }: { t: TruckRow }) {
  return (
    <li className="rt-truck">
      <Truck size={18} aria-hidden className="rt-truck-ico" />
      <div className="rt-truck-main">
        <div><strong className="rt-plate">{t.plate}</strong> <span className="muted">{t.vehicle.split(" · ")[1] ?? ""}</span></div>
        <div className="small rt-driver"><UserRound size={14} aria-hidden /> {t.driver ?? <span className="muted">водитель не закреплён</span>}</div>
        {t.note && <div className="small muted">{t.note}</div>}
      </div>
      <div className="rt-badges">
        {t.source === "date" && <Badge tone="info">Замена на дату</Badge>}
        {t.status !== "in_work" && <Badge tone="warning">{t.status_name || "Не на линии"}</Badge>}
      </div>
    </li>
  );
}

export function RouteBoard() {
  const { can } = useAuth();
  const entity = useEntityParam();
  const [s, setS] = useUrlState({ date: "" });
  const q = useQuery({
    queryKey: ["route-board-day", s.date, entity],
    queryFn: () => get<Board>("/route-board/day", { date: s.date || undefined, legal_entity_id: entity }),
    placeholderData: (p) => p,
  });
  const d = q.data;
  const today = d?.today ?? "";
  const day = d?.date ?? s.date;
  const isToday = !!d && d.date === d.today;
  const go = (iso: string) => setS({ date: iso === today ? "" : iso });
  const canPlan = can("route_plan.create");
  const repair = (d?.routes ?? []).flatMap((r) => r.trucks.filter((t) => t.status !== "in_work").map((t) => ({ route: r.route, plate: t.plate })));

  return (
    <>
      <PageHeader
        title="Маршруты по дням"
        sub="Какой мусоровоз на каком маршруте едет в выбранный день, кто за рулём и по каким улицам"
        actions={<>
          {can("route_plan.view") && <Link className="btn" to="/route-plan"><CalendarRange size={16} aria-hidden /> Расписание</Link>}
          {can("garbage_routes.view") && <Link className="btn" to="/garbage-routes"><Signpost size={16} aria-hidden /> Маршруты</Link>}
        </>}
      />
      <div className="stack">
        <Card className="rt-nav-card">
          <div className="rt-nav">
            <Button icon={ChevronLeft} aria-label="Предыдущий день" disabled={!day} onClick={() => day && go(shiftDay(day, -1))} />
            <div className="rt-nav-day">
              <strong>{isToday ? "Сегодня" : day ? longDay(day) : "…"}</strong>
              {isToday && <span className="muted small">{longDay(day)}</span>}
            </div>
            <Button icon={ChevronRight} aria-label="Следующий день" disabled={!day} onClick={() => day && go(shiftDay(day, 1))} />
          </div>
          <div className="rt-nav-tools">
            <label className="rt-date-pick">
              <span className="muted small">Другая дата</span>
              <input type="date" value={day || ""} min="2000-01-01" max="2100-01-01" onChange={(e) => e.target.value && go(e.target.value)} />
            </label>
            {!isToday && today && <Button onClick={() => go(today)}>Сегодня</Button>}
          </div>
        </Card>

        <QueryState q={q} what="Маршруты">
          {d && d.totals.routes_all === 0 && (
            <Card>
              <Empty icon={Signpost} title="Маршрутов пока нет">
                Добавьте маршруты в разделе «Маршруты» (название и улицы), затем поставьте на них мусоровозы в «Расписании маршрутов».
                {can("garbage_routes.create") && <div style={{ marginTop: 12 }}><Link className="btn" to="/garbage-routes?new=1">Добавить маршрут</Link></div>}
              </Empty>
            </Card>
          )}
          {d && d.totals.routes_all > 0 && (
            <>
              <p className="rt-total" aria-live="polite">
                {d.totals.routes
                  ? <>В этот день <strong>{d.totals.trucks} {plural(d.totals.trucks, "мусоровоз", "мусоровоза", "мусоровозов")}</strong> на <strong>{d.totals.routes} {plural(d.totals.routes, "маршруте", "маршрутах", "маршрутах")}</strong></>
                  : <>В этот день ни один мусоровоз не стоит в расписании</>}
              </p>
              {repair.length > 0 && (
                <Alert tone="warning">
                  В ремонте, но стоит в расписании: {repair.map((r) => `${r.plate} (${r.route})`).join(", ")}. Нужна замена{canPlan ? <>: добавьте в «<Link to="/route-plan?new=1">Расписании</Link>» запись «На одну дату»</> : ""}.
                </Alert>
              )}
              <div className="rt-grid">
                {d.routes.map((r) => (
                  <Card key={r.route_id} title={r.route} icon={Signpost} className="rt-card">
                    {r.note && <p className="muted small rt-note">{r.note}</p>}
                    <ul className="rt-trucks">{r.trucks.map((t) => <TruckLine key={t.plan_id} t={t} />)}</ul>
                    <Streets streets={r.streets} />
                  </Card>
                ))}
              </div>
              {d.routes_without_truck.length > 0 && (
                <Card title="Маршруты без мусоровоза в этот день" icon={Signpost}>
                  <ul className="rt-plain">
                    {d.routes_without_truck.map((r) => <li key={r.route_id}><strong>{r.route}</strong></li>)}
                  </ul>
                </Card>
              )}
              {d.trucks_without_route.length > 0 && (
                <Card title="Мусоровозы без маршрута в этот день" icon={Truck}>
                  <ul className="rt-plain">
                    {d.trucks_without_route.map((t) => (
                      <li key={t.vehicle_id}>
                        <strong className="rt-plate">{t.plate}</strong> <span className="muted">{t.driver ?? "водитель не закреплён"}</span>
                        {t.status !== "in_work" && <> <Badge tone="warning">{t.status_name || "Не на линии"}</Badge></>}
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
            </>
          )}
        </QueryState>
      </div>
    </>
  );
}
