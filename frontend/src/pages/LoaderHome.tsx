// Кабинет грузчика мусоровоза: главная страница человека с ролью «Грузчик мусоровоза».
// Сверху выход на работу и кнопка «Замечание по маршруту», ниже машина с водителем и напарниками, маршрут на сегодня и ближайшие дни,
// сколько машина вывезла (без денег) и свои замечания.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarRange, Hourglass, MessageSquareWarning, Signpost, Truck, UserRound, Weight, Wrench } from "lucide-react";
import { get, post } from "../api";
import { useAuth } from "../auth";
import { fmtDate, fmtNum, plural } from "../format";
import { Alert, Badge, Button, Card, Empty, Field, Modal, PageHeader, QueryState, VehicleStatus, useForm, useToast } from "../ui";
import { AttendanceCard } from "./Attendance";
import { longDay, Streets } from "./routeParts";

interface Tons { trips: number; tons: number; pending: number; days: number }
interface LVehicle {
  id: number; plate: string; label: string; status: string; status_label: string; driver: string | null; mates: string[];
  tonnage: boolean; today: Tons | null; month: Tons | null;
}
interface DayItem { route: string; streets: string[]; note: string; plate: string; vehicle_id: number }
interface Day { date: string; weekday: string; is_today: boolean; items: DayItem[] }
export interface RouteNote { id: number; date: string; kind: string; kind_label: string; street: string; note: string; plate: string; created_at: string | null; resolved: boolean; resolved_at: string | null; who?: string; can_resolve?: boolean }
interface Home {
  today: string; month_title: string; name: string; has_employee: boolean; has_vehicle: boolean; vehicles: LVehicle[]; days: Day[];
  notes: RouteNote[]; note_kinds: { code: string; label: string }[];
  can: { requests: boolean; attendance: boolean; worklog: boolean };
}

const longDate = (iso: string) => {
  const t = new Date(iso + "T12:00:00").toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  return t.charAt(0).toUpperCase() + t.slice(1);
};

/** Две буквы для кружка-аватара: «Орлов Антон» → «ОА». */
export function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((x) => x[0]?.toUpperCase()).join("");
}

function TruckCard({ v, monthTitle }: { v: LVehicle; monthTitle: string }) {
  return (
    <section className="card crew-card">
      <div className="crew-card-head">
        <span className="crew-truck-ico" aria-hidden><Truck size={20} /></span>
        <div className="job-title">
          <span className="plate-tag">{v.plate}</span>
          <span className="muted small">{v.label.replace(v.plate + " · ", "")}</span>
        </div>
        <VehicleStatus code={v.status} label={v.status_label} />
      </div>
      <ul className="plain-list crew-people">
        <li className="crew-person is-driver">
          <span className="crew-ava drv" aria-hidden>{v.driver ? initials(v.driver) : "?"}</span>
          <span className="crew-person-name"><strong>{v.driver || "Водитель не закреплён"}</strong><span className="muted small">водитель</span></span>
        </li>
        {v.mates.map((m) => (
          <li key={m} className="crew-person">
            <span className="crew-ava" aria-hidden>{initials(m)}</span>
            <span className="crew-person-name"><strong>{m}</strong><span className="muted small">напарник</span></span>
          </li>
        ))}
      </ul>
      {v.month && (
        <div className="facts">
          <div><span className="muted small">За {monthTitle.toLowerCase()}</span><strong><Weight size={14} aria-hidden /> {fmtNum(v.month.tons, 2)} т · {v.month.trips} {plural(v.month.trips, "ходка", "ходки", "ходок")} · {v.month.days} {plural(v.month.days, "день", "дня", "дней")}</strong></div>
        </div>
      )}
      {v.status === "in_repair" && <Alert tone="warning">Машина сейчас в ремонте. Уточните у диспетчера, где вы работаете сегодня.</Alert>}
      {v.today && v.today.pending > 0 && <p className="muted small">Талонов без веса за сегодня: {v.today.pending}. Вес внесут в офисе.</p>}
    </section>
  );
}

/** Плашка сверху: куда сегодня едем и сколько уже вывезли. */
function Hero({ h }: { h: Home }) {
  const today = h.days.find((d) => d.is_today);
  const routes = today?.items.map((x) => x.route) ?? [];
  const t = h.vehicles.reduce((a, v) => ({ tons: a.tons + (v.today?.tons ?? 0), trips: a.trips + (v.today?.trips ?? 0), has: a.has || !!v.today }), { tons: 0, trips: 0, has: false });
  const streets = today?.items.reduce((n, x) => n + x.streets.length, 0) ?? 0;
  return (
    <div className="card loader-hero">
      <div className="loader-hero-main">
        <span className="deco-title">Сегодня · {h.vehicles.map((v) => v.plate).join(", ")}</span>
        <strong style={{ fontSize: 22 }}>{routes.length ? routes.join(", ") : "Маршрута на сегодня нет"}</strong>
        <span className="muted small">{routes.length ? `${streets} ${plural(streets, "улица", "улицы", "улиц")} по маршруту` : "Уточните у диспетчера, куда едет машина"}</span>
      </div>
      <div className="loader-hero-stats">
        {t.has && <div className="loader-hero-stat accent"><strong>{fmtNum(t.tons, 1)}</strong><span>тонн</span></div>}
        {t.has && <div className="loader-hero-stat"><strong>{t.trips}</strong><span>{plural(t.trips, "ходка", "ходки", "ходок")}</span></div>}
        <div className="loader-hero-stat"><strong>{h.notes.filter((n) => !n.resolved).length}</strong><span>замечаний ждут</span></div>
      </div>
    </div>
  );
}

function NoteModal({ h, onClose }: { h: Home; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const f = useForm({ vehicle_id: String(h.vehicles[0]?.id ?? ""), kind: "not_taken", street: "", note: "" });
  const todayStreets = Array.from(new Set((h.days.find((d) => d.is_today)?.items ?? []).filter((x) => String(x.vehicle_id) === f.values.vehicle_id).flatMap((x) => x.streets)));
  const m = useMutation({
    mutationFn: () => post("/loaders/notes", { ...f.values, vehicle_id: Number(f.values.vehicle_id) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["loader-home"] }); toast.success("Замечание отправлено диспетчеру"); onClose(); },
    onError: f.fail,
  });
  const other = f.values.kind === "other";
  return (
    <Modal title="Замечание по маршруту" onClose={onClose}
      footer={<><Button onClick={onClose}>Отмена</Button><Button variant="primary" loading={m.isPending} onClick={() => { f.clear(); m.mutate(); }}>Отправить</Button></>}>
      <div className="stack">
        {f.formError && <Alert tone="error">{f.formError}</Alert>}
        {h.vehicles.length > 1 && (
          <Field label="Машина" error={f.errors.vehicle_id}>
            <select className="input" value={f.values.vehicle_id} onChange={(e) => f.set("vehicle_id", e.target.value)}>
              {h.vehicles.map((v) => <option key={v.id} value={v.id}>{v.plate}</option>)}
            </select>
          </Field>
        )}
        <Field label="Что случилось" error={f.errors.kind}>
          <div className="loader-kinds">
            {h.note_kinds.map((k) => (
              <button key={k.code} type="button" aria-pressed={f.values.kind === k.code} className="chip-btn" onClick={() => f.set("kind", k.code)}>{k.label}</button>
            ))}
          </div>
        </Field>
        <Field label={other ? "Улица, дом (по желанию)" : "Улица, дом"} error={f.errors.street} hint={todayStreets.length ? "Можно нажать улицу из сегодняшнего маршрута и дописать дом" : undefined}>
          <input className="input" value={f.values.street} maxLength={200} onChange={(e) => f.set("street", e.target.value)} placeholder="Например: Ленина 15" />
        </Field>
        {todayStreets.length > 0 && (
          <div className="loader-streets">
            {todayStreets.slice(0, 12).map((s) => <button key={s} type="button" className="chip-btn" onClick={() => f.set("street", s + " ")}>{s}</button>)}
          </div>
        )}
        <Field label={other ? "Что случилось (обязательно)" : "Подробнее (по желанию)"} error={f.errors.note}>
          <textarea className="input" value={f.values.note} maxLength={500} onChange={(e) => f.set("note", e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

export function NoteStatus({ n }: { n: RouteNote }) {
  return n.resolved ? <Badge tone="success">Разобрались</Badge> : <Badge tone="warning">Ждёт</Badge>;
}

export function LoaderHome() {
  const { me } = useAuth();
  const q = useQuery({ queryKey: ["loader-home"], queryFn: () => get<Home>("/loaders/home"), refetchInterval: 60_000 });
  const [note, setNote] = useState(false);
  const h = q.data;
  const today = h?.days.find((d) => d.is_today);
  const next = (h?.days ?? []).filter((d) => !d.is_today && d.items.length);
  const many = (h?.vehicles.length ?? 0) > 1;
  return (
    <>
      <PageHeader title={`Здравствуйте${h?.name || me?.display_name ? ", " + (h?.name || me?.display_name) : ""}`} sub={h ? longDate(h.today) : undefined} />
      <QueryState q={q}>
        {h && (
          <div className="stack">
            {h.can.attendance && h.has_employee && <AttendanceCard />}
            {!h.has_employee ? (
              <Card><Empty icon={UserRound} title="Учётная запись не привязана к сотруднику">Попросите администратора привязать вас к карточке сотрудника.</Empty></Card>
            ) : !h.has_vehicle ? (
              <Card><Empty icon={Truck} title="Вы пока не закреплены за мусоровозом">Попросите диспетчера закрепить вас за машиной: тогда здесь появятся водитель, маршрут и сколько вывезли.</Empty></Card>
            ) : (
              <>
                <Hero h={h} />

                <div className="entry-quick">
                  <Button variant="primary" size="lg" icon={MessageSquareWarning} onClick={() => setNote(true)}>Замечание по маршруту</Button>
                  {h.can.requests && <Link className="btn lg" to={h.vehicles.length === 1 ? `/requests?new=1&vehicle=${h.vehicles[0].id}` : "/requests?new=1"}><Wrench size={16} aria-hidden /> Поломка машины</Link>}
                  {h.can.worklog && <Link className="btn lg" to="/my-time"><Hourglass size={16} aria-hidden /> Мои часы</Link>}
                </div>

                <div className="grid cols-2 vehicles-grid">{h.vehicles.map((v) => <TruckCard key={v.id} v={v} monthTitle={h.month_title} />)}</div>

                <Card title="Маршрут на сегодня" icon={Signpost}>
                  {today && today.items.length ? (
                    <div className="rt-list">
                      {today.items.map((it, i) => (
                        <div key={i} className="rt-route">
                          <div className="rt-route-head"><strong className="rt-name">{it.route}</strong>{many && <Badge>{it.plate}</Badge>}</div>
                          {it.note && <div className="muted small">{it.note}</div>}
                          <Streets streets={it.streets} open />
                        </div>
                      ))}
                    </div>
                  ) : <Empty icon={Signpost} title="На сегодня маршрута нет">Расписание составляет диспетчер. Если вы выходите, спросите у него, куда едет машина.</Empty>}
                </Card>

                {next.length > 0 && (
                  <Card title="Ближайшие дни" icon={CalendarRange}>
                    <div className="rt-days">
                      {next.map((d) => (
                        <section key={d.date} className="rt-day">
                          <h3><span className="rt-wd">{d.weekday}</span> {longDay(d.date).split(", ")[1]}</h3>
                          <p className="small">{d.items.map((x) => x.route + (many ? ` (${x.plate})` : "")).join(", ")}</p>
                        </section>
                      ))}
                    </div>
                  </Card>
                )}

                <Card title="Мои замечания" icon={MessageSquareWarning}>
                  {h.notes.length ? (
                    <ul className="plain-list loader-notes">
                      {h.notes.map((n) => (
                        <li key={n.id}>
                          <div className="row wrap" style={{ gap: 8 }}><strong>{n.kind_label}</strong><NoteStatus n={n} /></div>
                          <span className="muted small">{fmtDate(n.date)} · {n.plate}{n.street ? ` · ${n.street}` : ""}</span>
                          {n.note && <span className="small">{n.note}</span>}
                        </li>
                      ))}
                    </ul>
                  ) : <p className="muted">Не вывезли бак, переполнено, сломан контейнер или не проехать? Нажмите «Замечание по маршруту», и диспетчер сразу узнает.</p>}
                </Card>
              </>
            )}
          </div>
        )}
      </QueryState>
      {h && note && <NoteModal h={h} onClose={() => setNote(false)} />}
    </>
  );
}
