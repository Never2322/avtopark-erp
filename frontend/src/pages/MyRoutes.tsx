// «Мои маршруты»: что водителю мусоровоза ехать сегодня, в ближайшие дни и по каким дням недели обычно.
// Маршруты и расписание ведёт диспетчер или директор; здесь только чтение.
import { useQuery } from "@tanstack/react-query";
import { CalendarRange, Signpost } from "lucide-react";
import { get } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, Empty, PageHeader, QueryState } from "../ui";
import { longDay, Streets } from "./routeParts";

interface Item { plan_id: number; route_id: number; route: string; streets: string[]; route_note: string; vehicle_id: number; plate: string; vehicle: string; source: "weekly" | "date"; note: string }
interface Day { date: string; weekday: string; is_today: boolean; items: Item[] }
interface WeekItem { route_id: number; route: string; streets: string[]; vehicle: string }
interface Mine { today: string; vehicles: { id: number; label: string }[]; days: Day[]; week: { weekday: string; index: number; items: WeekItem[] }[]; has_any: boolean }

function RouteCard({ it, open, many }: { it: Item; open?: boolean; many: boolean }) {
  return (
    <div className="rt-route">
      <div className="rt-route-head">
        <strong className="rt-name">{it.route}</strong>
        {it.source === "date" && <Badge tone="info">Замена на этот день</Badge>}
      </div>
      {many && <div className="muted small">{it.vehicle}</div>}
      {it.note && <div className="small">{it.note}</div>}
      {it.route_note && <div className="muted small">{it.route_note}</div>}
      <Streets streets={it.streets} open={open} />
    </div>
  );
}

export function MyRoutes() {
  const { me } = useAuth();
  const enabled = !!me?.features?.routes;
  const q = useQuery({ queryKey: ["route-board-mine"], queryFn: () => get<Mine>("/route-board/mine"), enabled, refetchInterval: 5 * 60_000 });
  const d = q.data;

  if (!enabled) {
    return (
      <>
        <PageHeader title="Мои маршруты" />
        <Card><Empty icon={Signpost} title="Маршруты есть у водителей мусоровозов">Они появятся, когда за вами закрепят мусоровоз.</Empty></Card>
      </>
    );
  }
  const today = d?.days.find((x) => x.is_today);
  const next = (d?.days ?? []).filter((x) => !x.is_today);
  const many = (d?.vehicles.length ?? 0) > 1;
  const withRoutes = next.filter((x) => x.items.length);
  const free = next.length - withRoutes.length;
  return (
    <>
      <PageHeader title="Мои маршруты" sub="В какой день какой маршрут и по каким улицам. Расписание составляет диспетчер: если что-то изменилось, здесь это появится само" />
      <QueryState q={q} what="Маршруты">
        {d && (
          <div className="stack">
            <Card title="Сегодня" icon={Signpost}>
              <div className="muted small rt-date">{longDay(d.today)}</div>
              {today && today.items.length ? (
                <div className="rt-list">{today.items.map((it) => <RouteCard key={it.plan_id} it={it} open many={many} />)}</div>
              ) : (
                <Empty icon={Signpost} title="Сегодня маршрута нет">{d.has_any ? "Смотрите ближайшие дни ниже." : "Расписание для вашей машины пока не составлено: спросите у диспетчера."}</Empty>
              )}
            </Card>

            {d.has_any && (
              <Card title="Ближайшие дни" icon={CalendarRange}>
                {withRoutes.length ? (
                  <div className="rt-days">
                    {withRoutes.map((day) => (
                      <section key={day.date} className="rt-day">
                        <h3><span className="rt-wd">{day.weekday}</span> {longDay(day.date).split(", ")[1]}</h3>
                        <div className="rt-list">{day.items.map((it) => <RouteCard key={it.plan_id} it={it} many={many} />)}</div>
                      </section>
                    ))}
                  </div>
                ) : <p className="muted">В ближайшие две недели маршрутов нет.</p>}
                {free > 0 && withRoutes.length > 0 && <p className="muted small rt-free">В остальные дни из ближайших двух недель маршрута нет.</p>}
              </Card>
            )}

            {d.week.some((w) => w.items.length) && (
              <Card title="Обычное расписание по дням недели" icon={CalendarRange}>
                <div className="rt-week">
                  {d.week.map((w) => {
                    const isNow = w.weekday === today?.weekday;
                    return (
                      <section key={w.index} className={`rt-wk ${isNow ? "now" : ""}`} aria-current={isNow ? "date" : undefined}>
                        <h3>{w.weekday}{isNow && <span className="muted small"> · сегодня</span>}</h3>
                        {w.items.length ? w.items.map((i) => (
                          <div key={i.route_id} className="rt-wk-item">
                            <strong>{i.route}</strong>
                            <Streets streets={i.streets} />
                          </div>
                        )) : <span className="muted">Маршрута нет</span>}
                      </section>
                    );
                  })}
                </div>
              </Card>
            )}
          </div>
        )}
      </QueryState>
    </>
  );
}
